# Krump Track Finder Privacy Policy

Effective October 22, 2026 (from extension 1.2.0) · What changed from the previous policy: section 9 · The same document inside the extension: `extension/privacy.en.html` · [Korean](privacy-policy.md)

This is a translation of the [Korean policy](privacy-policy.md), with the same content and the same effective date.

Krump Track Finder (the “extension”) is a Chrome extension that listens to music playing in a browser tab and finds
the same track in a krump music library. This document explains what information the extension sends to the server,
and how that information is used and kept. The extension can be used only after agreeing to this policy.

## 1. Information sent to and stored on the server

The extension sends information to the server only in the two cases below. Otherwise it does not read a tab's audio
or your browsing history.

- **When you open the popup on a YouTube or Instagram video tab**: it sends the video's id (for example the `v=` value
  of a YouTube address) to look up the tracks other users found in this video. This lookup is not stored on the server.
- **When you press “Find the playing track”**: it sends and stores the information below.

| Information | Details |
|---|---|
| Tab audio recording | The audio of the tab you searched in, up to 1 minute (up to 30 seconds on the first search; 45 seconds or 1 minute if you choose it when searching again after nothing was found) |
| Tab title and address (URL) | The title and address of the tab you searched in |
| Search result | The track found, scores, search method, time |
| Result rating | Your answer, if you press Right or Wrong at “Is this right?” |

No name, email, account, cookies, location or similar information is collected. Stored recordings are not linked to
any person.

## 2. Purposes

- To find the track in the recording and show the result
- To improve the accuracy of finding tracks (tuning the search criteria, test data)
- To show other users watching the same video which tracks were found in that video (section 3-1)

The information is not used for any other purpose, such as advertising, sale or credit scoring. User data is not sold,
and apart from the information listed in section 3-1 it is not passed to any other company or person.

## 3. Where it is stored and for how long

- It is stored encrypted in private storage in the Amazon Web Services (AWS) Seoul region.
- Recordings are kept as long as they are needed to improve accuracy and deleted when no longer needed. Per-video track
  records (section 3-1) are kept as long as this feature is offered.
- To have a recording deleted, send the date and time of the recording and the video's address to the contact below.
  The recording and the per-video track records made from it are deleted together.

## 3-1. Information shown to other users

Of the results found, the information below is shown to other users who open the popup on the same video.

- Shown: the video id, the track found, the position in the track (where the preview starts), and a “found” or
  “confirmed by another user” mark
- Results that were not certain and that nobody confirmed as right, and searches that found nothing, are not shown.
  Results rated wrong are not shown either.
- Not shown: the recording, the tab title, the full video address, the date, time and number of searches, or who
  searched. The extension does not collect names, accounts or IP addresses, so the server does not know who searched
  either.
- This information can be looked up only by someone who knows the video id, and it is not published as a list.

## 4. Information stored only in the browser

Whether you agreed (the effective date of the policy you agreed to), the state of a search in progress, the library's
album list, and the record of your own results (video id, track, result, the latest 100) are stored only in your browser
(Chrome storage) and are not sent to the server.

## 5. Outside services

- Fonts are loaded from Google Fonts, and album covers on the library page from the image servers of the stores
  (Bandcamp and others). Like any web request, these services may receive your IP address and similar information.
- Pressing “Buy the album” or “See the artist” opens the store's site. That site's own policy applies there.

## 6. Permissions

- **tabCapture**, **activeTab**, **offscreen**: record the audio of the tab you searched in (you keep hearing it while
  it records).
- **storage**: store the information of section 4 in the browser.
- Access to the server address: send the recording to the search server and receive the result.

## 7. Withdrawing consent

Pressing “Withdraw consent” at the bottom of the popup withdraws it, and searching is not available until you agree
again. The record of your own results stored in the browser (section 4) is deleted at the same time. To have recordings
and per-video track records already stored deleted, ask as described in section 3.

## 8. Contact and changes

Questions and deletion requests: kjh960912@gmail.com

When this policy changes, this page gives the new effective date and what changed (section 9), and the extension asks
for your consent again. Searching is not available until you agree again.

## 9. Revision history

- **Effective October 22, 2026 (extension 1.2.0)**
  - The longest tab audio recording went from 45 seconds to 1 minute. The first search listens for up to 30 seconds;
    only when searching again after nothing was found can you choose 45 seconds or 1 minute.
  - The chosen recording length is no longer stored in the browser.
  - Opening the popup on a video tab sends the video id to look up tracks other users found (not stored).
  - Tracks found are shown to other users watching the same video (section 3-1).
  - The record of your own results is stored in the browser and deleted when you withdraw consent.
  - Consent is asked again when the policy changes. Users who agreed to the previous policy must agree to this one
    again to search.
- **Effective October 17, 2026 (extension 1.0.0)**: first version.
