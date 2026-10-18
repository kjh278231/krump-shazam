"""Turn a raw Panako candidate list into songs and a verdict.

- Candidates that contain the same recording (another album's copy, a mix or session tape that
  includes the track) are grouped, using the links in db/same_audio.csv (find_duplicates.py).
  Grouping only follows direct links, never chains, so a mix that contains two different
  songs doesn't merge them.
- Preview/promo files (snippets of an album's tracks) are hidden behind the real track when
  both matched; a song found only in a preview is still reported, flagged as such.
- Verdict: confirmed when the best song scores >= CONFIRM_SCORE and no *different* song comes
  close (krump producers share loops and samples); check when the score is low or a rival is close.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field

from common import ROOT, Match

LINKS_FILE = ROOT / "db" / "same_audio.csv"
PREVIEW_RE = re.compile(r"preview|snippet|teaser|medley|sampler|promo|trailer", re.I)
# Full-track self-queries: real shared recordings overlap for tens of seconds with scores in the
# hundreds; shared drum loops/one-shots give < 25 over ~5-10 s.
LINK_SCORE = 30
LINK_SECONDS = 15
CONFIRM_SCORE = 10
CHECK_SCORE = 5
RIVAL_RATIO = 0.6


def load_links(path=LINKS_FILE) -> set[frozenset]:
    links = set()
    if not path.exists():
        return links
    with path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if int(r["score"]) >= LINK_SCORE and float(r["seconds"]) >= LINK_SECONDS:
                links.add(frozenset((int(r["id"]), int(r["other_id"]))))
    return links


def is_preview(m: Match) -> bool:
    return bool(PREVIEW_RE.search(m.match_path.replace("\\", "/").rsplit("/", 1)[-1]))


@dataclass
class Song:
    members: list[Match] = field(default_factory=list)

    @property
    def score(self) -> int:
        return max(m.score for m in self.members)

    @property
    def preview_only(self) -> bool:
        return all(is_preview(m) for m in self.members)

    @property
    def head(self) -> Match:
        """Best-scoring real track; a preview only when nothing else matched."""
        real = [m for m in self.members if not is_preview(m)]
        return (real or self.members)[0]


def group(matches: list[Match], links: set[frozenset]) -> list[Song]:
    songs: list[Song] = []
    for m in sorted(matches, key=lambda m: m.score, reverse=True):
        home = next((s for s in songs if any(
            x.match_id == m.match_id or frozenset((x.match_id, m.match_id)) in links for x in s.members)), None)
        if home:
            home.members.append(m)
        else:
            songs.append(Song([m]))
    return songs


def verdict(songs: list[Song], confirm: float = CONFIRM_SCORE, check: float = CHECK_SCORE) -> tuple[str, str]:
    """(verdict, reason) with verdict in confirmed / check / fail. The scores default to Panako's."""
    if not songs or songs[0].score < check:
        return "fail", ""
    top = songs[0]
    rival = songs[1] if len(songs) > 1 else None
    if rival and rival.score >= RIVAL_RATIO * top.score:
        return "check", "rival"
    if top.score < confirm:
        return "check", "low"
    return "confirmed", ""
