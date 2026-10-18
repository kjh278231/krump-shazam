# 앨범 구매 링크와 커버 (`db/album_links.json`)

곡을 찾았을 때 앨범 커버와 구매 페이지를 같이 보여 주기 위한 데이터. 대부분 밴드캠프이고, KNKRT/Konkrete는
자체 사이트(knkrtworld.com), Soulfabex는 Gumroad에서 판다. 키는 `db/albums.json`의 앨범 id.

```json
"6451bfa20103": {"url": "https://10xgully.bandcamp.com/album/god-like-iv", "kind": "album", "store": "bandcamp",
                 "title": "GOD LIKE IV", "cover": "https://f4.bcbits.com/img/a2586217811_10.jpg"}
```

- `kind: album`: 앨범 페이지(구매 가능). `kind: artist`: 앨범 페이지를 못 찾아 아티스트 페이지로 연결.
- `store`: `bandcamp`, `knkrtworld`, `gumroad`. `title`: 판매 페이지에 적힌 앨범 제목.
- `cover`: 판매 페이지의 커버. 밴드캠프는 끝 번호로 크기를 고른다(`_9` 210px, `_16` 700px, `_10` 1200px).
  화면에는 `python server/fetch_store_covers.py`가 받아서 줄여 둔 `db/store_covers/<id>.jpg`(144px)를 쓴다.
  커버가 없으면 기존 `db/covers/<id>.jpg`(태그에 들어 있던 120px 커버)를 쓴다.

## 화면에 쓰는 곳

- 팝업: 곡을 찾으면 서버(`/identify`)가 가장 유력한 곡에 `album`(제목, 아티스트, 커버 data URI, `store`)을
  붙인다. 팝업은 커버와 함께 "밴드캠프에서 앨범 구매 ↗"(`kind: artist`면 "…에서 아티스트 보기 ↗") 버튼을
  보여 준다. 링크가 없으면 버튼을 숨긴다.
- 찾은 파일이 묶음 폴더(`단일 곡`, `트랙리스트` 등)에 있으면 같은 음원의 다른 사본에서 앨범을 찾는다.
  fingerprint DB는 바이트가 같은 사본을 한 번만 저장하므로 `db/albums.json`의 트랙별 Panako id(`ids`)로,
  다시 인코딩된 사본은 `db/same_audio.csv`로 찾는다. 링크가 있는 앨범을 먼저 고른다.
- 라이브러리 페이지(`/albums`): 앨범 카드마다 "구매 ↗" 또는 "아티스트 ↗" 링크.

## 현황 (2026-10-16 확인)

| | 앨범 수 |
|---|---|
| 밴드캠프 앨범 페이지 | 242 |
| 밴드캠프 아티스트 페이지로 대체 | 45 |
| knkrtworld.com 앨범 페이지 | 9 |
| knkrtworld.com 음악 페이지로 대체 (Konkrete, KNKRT의 WILDLYFE 2개) | 6 |
| Gumroad 상품 페이지 (Soulfabex TEST YOUR MIGHT) | 1 |
| Gumroad 상점으로 대체 (Soulfabex 나머지 7개) | 7 |
| 링크 없음 | 9 |
| 전체 | 319 |

2026-10-18에 링크 없던 앨범 6개를 더 찾았다(트랙 목록으로 확인).
- TRUST의 EPIC KILLA, Metamorphosis와 TrustBeatz의 InKarnation: 지금 이름은 MOWGLI(이탈리아),
  mowglibeatz.bandcamp.com.
- M1ND FR3AKZ BR41N D4M4G3: mf13.bandcamp.com. 스토어 쪽은 20곡이고, 라이브러리에는 1번 트랙(Buckmouth_SAVAGES)이 없다.
- Kob의 Forastero: Koba(콜롬비아), kobakrump.bandcamp.com.
- 태그 없는 폴더 `2 COMPLIKATED PT2`: Flipside(Madrootz)의 2 COMPLIKATED PART II.

남은 링크 없는 9개:
- 폴더 정리용 묶음 6개: `트랙리스트`, `단일 곡`, `미발매 트랙`, `20190630신곡`, `tracks`, `HipHop beats`.
- 82K Recordz의 82KR : 사딸라!: 앨범 판매처는 없다. Pied Piper는 Pluton의 Endgame: World Edition에,
  Rumble RMX는 TED(cart97)의 U.R.BeatTED에, Emergency는 Cloud_D(cloudd0603.bandcamp.com)의 Bvck Monster에
  들어 있다.
- Daisy & Encore의 I Know: 판매처를 못 찾았다.
- Alphabex의 The Biginning: 판매처를 못 찾았다. YouTube 재업로드만 있다.

## 만든 방법

1. 밴드캠프에서 받은 MP3는 주석(COMM) 태그에 `Visit https://<artist>.bandcamp.com`이 들어 있다. 319개 중
   262개 앨범에서 아티스트 계정을 얻었다.
2. 앨범 주소는 대개 제목에서 만들어진다: 소문자로 바꾸고 영문·숫자가 아닌 글자는 `-`로 바꾼 뒤 이어진 `-`를
   하나로 합친다(`MAELSTRÖM` → `maelstr-m`, `π Afro Samurai π` → `afro-samurai`). 제목을 나중에 바꾼 앨범은
   예전 주소가 그대로 남는다.
3. 그렇게 만든 주소를 하나씩 열어 제목과 커버를 확인했다. 없는 페이지(404)는 검색, 아티스트의 다른 계정 확인
   으로 찾았다.

밴드캠프는 스크립트로 직접 요청하면 봇 확인 페이지("Client Challenge")를 돌려준다. 이를 우회하지 않고 일반 웹
가져오기 도구로 페이지를 하나씩 확인했다. 그래서 자동 갱신 스크립트는 없다.

## 주의

- **계정 주소는 바뀌고, 남이 가져가기도 한다.**
  - Kid NY의 kid-n-wyllinmusic 계정은 사라졌고, 앨범은 lukaaustin.bandcamp.com으로 옮겨졌다.
  - EEST/300의 옛 계정 m300sk는 비었고, 앨범은 eestmusic.bandcamp.com(레이블 Savage Kulture)에 있다.
  - tighteyez.bandcamp.com은 위조 문서를 파는 스팸 페이지가 됐다. 앨범은 officialtighteyex.bandcamp.com에 있다.
  - Ted(T)는 cart97.bandcamp.com에서 판다(사용자가 알려 줌).
  - Braze는 braze-uk.bandcamp.com에서 판다(2026-10-18, 사용자가 알려 줌).
  - 사라진 계정 이름은 누구나 다시 만들 수 있다. 링크는 주기적으로 다시 확인해야 한다(`checked` 날짜).
- SOJU의 NEW ERA(ssojuu.bandcamp.com/album/new-era)는 브라우저에서는 열리지만 우리 쪽 도구에는 404가 나온다.
  사용자가 확인한 링크라 그대로 두었다(`note`).
- 밴드캠프에서 제목이 바뀐 앨범이 2개 있다. `note`에 적어 두었다(UNDENIABLE → Void Hopper,
  Boi Gameover Part 1 → GAME IS OVER (VOL.1)).

## knkrtworld.com (KNKRT / Konkrete)

밴드캠프가 아니라 Bandzoogle로 만든 자체 사이트다. 앨범 페이지는 `/album/<숫자>/<slug>` 형식인데, 음악 목록
페이지에는 트랙 링크만 있다. 그래서 각 앨범 첫 트랙 페이지의 "From the recording" 링크로 앨범 주소를 얻었다.
라이브러리의 폴더 두 개는 이름이 다르다.
- `it's just buck-KNKRT(Secret tracks)`: ITZ JUST BUCK과 10곡이 모두 같다.
- `KNKRT(Secret tracks)`: 8곡 중 4곡만 SELF DESTRUK에 있다(`note`에 적어 둠).

`WILDLYFE`, `wildlyfe vol2` 폴더는 KNKRT 앨범이다(사용자가 알려 줌). 태그에 아티스트가 없어서 링크 항목의
`artist`(KNKRT)를 서버가 앨범 아티스트로 쓴다. knkrtworld.com의 WLDLF는 곡이 달라서 음악 페이지로 연결한다.

## Gumroad (Soulfabex)

soulfabexmusic.gumroad.com은 판매자가 꾸민 소개 페이지이고, 상품 목록은 페이지 원본의
`<script id="gumroad-data">` JSON에 들어 있다(이름, 주소, 가격, 커버). 2026-10-16 기준 상품 9개 중 라이브러리와
같은 앨범은 TEST YOUR MIGHT 하나다. 상점의 Reckoning II(3부작)는 라이브러리의 Reckoning(SOULFABANGX, 21곡)의
후속작이라 연결하지 않았다. 나머지 Soulfabex 앨범 7개는 상점 첫 페이지로 연결했다.

## 스트리밍 (2026-10-16 확인)

링크 없는 앨범을 Apple Music(iTunes Search API), Spotify·YouTube(웹 검색)에서 찾아봤지만 같은 앨범은 없었다.
Apple Music에는 Savage Kulture(32개), Big Rulez(13개), Tighteyex(9개), Rucboi(5개) 등의 카탈로그가 있어서,
이미 구매 링크가 있는 앨범 일부는 스트리밍 링크를 더 붙일 수 있다.

