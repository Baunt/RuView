# Mérőállomás 0.1

## Működés

FastAPI háttérszolgáltatás, React/TypeScript felület, SQLite-metaadatok, tömörített eredeti API-megfigyelések, FIT-import és külön, standard Python könyvtárral futó UDP-collector. Ez az adatgyűjtési és összehasonlítási mérföldkő; nincs benne paraméterkeresés, új pulzusbecslő, firmware-írás vagy profiltelepítés.

Az adapter csak GET-kéréseket küld a RuView `/health`, `/api/v1/nodes`, `/api/v1/calibration/status` és `/api/v1/vital-signs` végpontjaira. Egy ciklus után 1 s szünet következik; a ciklus időtartama és a fogadási idő minden megfigyelés része. Az eredeti válaszok is megmaradnak. A token Authorization fejlécben utazik, nem URL-ben, naplóban, SQLite-ban vagy ZIP-ben.

2026-11-20-tól az adapter opcionális diagnosztikaként a `/api/v1/sensing/latest` eredeti válaszát is megőrzi (`responses.sensing`), benne az elérhető kalibrált jelenlétbizonyítékkal és detektordiagnosztikával. Ez külön forrás a node heurisztikus személyszámától, nem helyettesíti azt és nem garantál szobaszintű lokalizációt. Az opcionális végpont hibája megmarad a naplóban, de nem tiltja le a négy kötelező végpont alapján már jogosított publikus vitals megőrzését. A kötelező auth/frissesség/forrás/publikációs kapuk változatlanok.

Érvényes publikus pulzushoz élő `esp32` forrás, 3 s-nál frissebb feldolgozás és UDP-forgalom, a kiválasztott egyetlen friss CSI-node és a szerver `explicit_calibration` publikációs jogosultsága kell. Stale, szimulált, több node-hoz köthető és visszatartott érték nem kerül érvényes összehasonlításba. A RuView kapuit az alkalmazás nem módosítja. A `signal_quality` heurisztika, nem pontossági valószínűség.

A publikus végpont nem közöl per-becslés ablakvéget, kibocsátási időt, ablakméretet és profilazonosítót. Ezeket `null`-ként őrizzük; a forrás neve `ruview_public_vitals`. A riport **API-fogadási idő szerinti megfigyelési összehasonlítás**. A szerveres és az edge algoritmus egyezését nem állítja. Az időbélyegek hiányát nem pótolja sejtett ablakmérettel.

## Tárolás

Alapkönyvtár: `data/`, felülírható az `AUTOTUNER_DATA` változóval. SQLite WAL mód, gzip JSONL API-válaszok, egyedi nevű eredeti FIT-ek és nyers felvételek. Egy alkalmazáspéldányt indíts; egyszerre egy felvétel futhat, legfeljebb 8 óráig. Az `AUTOTUNER_PORT` változóval a helyi 8787-es port módosítható.

Normál leállításkor a stream lezárul. Rendellenes kilépés után az előző felvétel `interrupted`, az SQLite-megfigyelések megmaradnak, végideje az utolsó mentett megfigyelés. A hiányos gzip nem minősül lezárt eredetinek. A rekord UTC és monotonic fogadási időt is őriz. A session kezdeti órakötésétől 100 ms-nál nagyobb óralépés a rekordot érvényteleníti.

Új referencia-hozzárendelés nem törli az előző eredeti fájlt; az aktuális pálya a `reference.json` fájlban van. A ZIP a sessiont, eredeti fájlokat, referenciapályát, JSON-riportot, `timeline.csv`-t és az eredeti artifactok SHA-256 értékét tartalmazza. A referenciaillesztés ideje és policy verziója része a riportnak. Módosítás előtt mentsd le az előző ZIP-et, ha az előző értékelést meg akarod őrizni.

## FIT és időillesztés

A FIT-adapter a [fitdecode](https://github.com/polyvertex/fitdecode) szigorú CRC-ellenőrzését használja; csak `record` üzenetek időbélyeges pulzusát olvassa. A FIT UTC időalapját használja, nem a Mac helyi időzónáját. Hiányzó vagy invalid HR hiány marad. Azonos időbélyegű ellentmondó rekordból nem választ egyet: az időpont érvénytelen. Minőségi számlálók: tényleges mintaköz, rések, ismétlődések, hiányok, időrendi visszalépések. A fájl eszközmetaadata megmarad; ismeretlen gyártót nem igazolunk Garminnak.

Előjel: `station_utc = fit_utc + offset_s`. Egy kézi eltolás érvényes minden időpontra; nincs hibaminimalizáló lag-keresés. A `uncertainty_s` ismeretlenül hagyható, ez látható korlát. A Garmin belső simítási késése külön ismeretlen. Közös kezdő- és záróesemény segít a drift felismerésében; az első változat nem korrigál óradriftet.

## Riport: public-observation-v1

- Rögzített UTC 1 s-os rács a felvétel első teljes másodpercétől az utolsóig.
- Referencia: legközelebbi rekord legfeljebb ±0,5 s-ra; nincs interpoláció. Egyező távolságnál a korábbi nyer.
- RuView: az adott rácsidőig már fogadott utolsó megfigyelés, legfeljebb 1,5 s-os korral. Visszatartott/érvénytelen/órát lépő érték hiány marad. Jövőbeli válasz nem vetül vissza.
- A nyugalmi nevezőt az operátor ülő/fekvő blokkjai adják, a RuView-tól függetlenül. Mozgás, üres szoba és jelöletlen blokk nem jogosult nyugalmi értékelésre; a teljes idő az exportban megmarad.
- Értékelhető referencia: érvényes HR a kutatott 40–120 ütés/perc sávban. A kívül eső idő külön számláló.
- Lefedettség: párosított RuView-másodperc / összes értékelhető referenciájú nyugalmi másodperc. A visszatartott becslés a nevezőben marad.
- MAE: abszolút eltérések átlaga; P95: nearest-rank 95. percentilis; bias: RuView mínusz referencia átlaga. Párosítás hiányában `null`, nem 0 hiba.
- A teljes időre számolt RuView-lefedettség és a referencia-idő külön szerepel. Légzési MAE nincs légzési referencia nélkül.

Eredmény: `insufficient_data` vagy `observational_only`. Nyers I/Q nélkül a későbbi keresés állapota `raw_capture_required`. Egy csatolt nyers fájl önmagában nem algoritmusvalidáció: a hostok óraszinkronja, a becslési idő és a közös visszajátszó engine még igazolandó.

## Nyers UDP-collector

A [RuView UDP tee](https://github.com/ruvnet/RuView/blob/441349137008ce30ec0f147ccba0d4aaf65ddee5/v2/crates/wifi-densepose-sensing-server/src/udp_tee.rs) az engedélyezett datagramokat másolja loopback célra. A szerver kapcsolója `--udp-tee 127.0.0.1:5007`, illetve `RUVIEW_UDP_TEE`. A collector **ugyanabban a hálózati névtérben** figyeljen. Dockeres RuView mellett a Mac localhostja másik névtér. A RuView 5005-ös fogadóportját nem foglalhatja el.

Az `autotuner/raw.py` önállóan másolható a megfelelő gépre, nincs külső függősége. Példa azonos névtérben:

```sh
python3 raw.py --bind 127.0.0.1 --port 5007 --seconds 1200 --output pilot.jsonl.gz
```

Linuxon egy már konfigurált konténer hálózati névterében a host Pythonja is használható: `sudo nsenter --target <RuView-host-PID> --net python3 <raw.py-útvonala> ...`. Ez host fájlrendszert és órát használ. Előbb ellenőrizd a valódi PID-t, a tee konfigurációját, a szabad portot és a tárhelyet. A parancs nem kapcsolja be a tee-t; a konténer újraindítása külön telepítési lépés. A futó RuView konfigurációját a projekt nem módosította.

A parser a [vizsgált ADR-018 upstream kódot](https://github.com/ruvnet/RuView/blob/441349137008ce30ec0f147ccba0d4aaf65ddee5/v2/crates/wifi-densepose-sensing-server/src/csi.rs#L273) követi: 20 bájtos fejléc, 16 bites alvivőszám, node, antennák, frekvencia, sorszám, RSSI, PPDU/flags és változatlan I/Q-payload. Ismeretlen datagram is megmarad. Sorszámhiány, visszalépés és rácsváltozás új szakaszt indít; a visszalépés nem bizonyított reboot. Eszközidőt nem találunk ki.

Formátum: gzip JSONL header → datagramok → footer. Datagramonként base64 eredeti payload, SHA-256, host UTC + monotonic idő, peer, metaadatok és szakaszinformáció. A lezárás fájlhash-t és manifestet készít. Csatoláskor újraszámoljuk a payload-hash-eket, kereteket és sorszámhiányokat. Footer nélküli, sérült, más node-hoz vagy UTC-időtartományhoz tartozó felvételt nem fogadunk el. Az UTC-átfedés nem igazol óraszinkront. Limit: 40 MB gzip, 256 MB kicsomagolva; hosszabb felvételekhez több rövidebb mérési alkalom használható.

## Helyi adatok

A helyi mérési eredmények, kalibrációs receiptek és telepítési naplók nem részei a forrásrepónak.
