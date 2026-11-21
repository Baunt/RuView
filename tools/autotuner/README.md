# RuView AutoTuner

Helyben futó mérőállomás a RuView pulzus- és légzésbecslésének referencia alapú vizsgálatához. A jelenlegi változat API-felvételt, eseményjelölést, Garmin FIT-importot, rögzített időillesztést, összehasonlító riportot és külön nyers UDP-collectort tartalmaz. Paraméterkeresés és profiltelepítés még nincs benne.

## Indítás

Python 3.11+ és Node 20.19+ / 22.12+ szükséges. A parancsokat ebből a mappából futtasd:

```sh
cd tools/autotuner
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
npm --prefix web ci
npm --prefix web run build
sh ./scripts/start-station.sh
```

Megnyitás: [http://127.0.0.1:8787](http://127.0.0.1:8787). A szerver csak a helyi gépen figyel. Leállítás: Ctrl+C.

A Kapcsolat panelen add meg a RuView szerver címét, a kiválasztott node azonosítóját és szükség esetén a helyi forrás-IP-t. Új telepítésnél az alapcím `http://127.0.0.1:3080`, forrás-IP nincs megkötve. A korábban mentett helyi kapcsolat megmarad a `data/connection.json` fájlban.

A meglévő API-token csak memóriába kerül. Újraindításkor ismét megadható, vagy 0600 jogosultságú fájlból a `RUVIEW_TOKEN_FILE` változóval tölthető be. A token nem kerül a mérési exportba.

Az adatkönyvtár alapból `data/`, az `AUTOTUNER_DATA` változóval módosítható. A FIT-fájlok, nyers CSI-felvételek, adatbázisok, tokenek, képernyőképek és személyes mérési naplók helyi adatok; ne commitold őket.

## Első mérés

1. Ellenőrizd a RuView-kapcsolatot, a node-ot és az adatfrissességet. A publikus számkiadás és a kalibráció feltételeit a RuView szabályozza.
2. Indíts pulzust rögzítő Garmin-tevékenységet és állomásfelvételt. Jelöld az ülő/fekvő nyugalmi, mozgásos és üres szobás blokkokat, valamint a közös kezdő- és záróeseményt.
3. Zárd le a felvételt. Garmin Connectből exportáld az eredeti tevékenységfájlt, és importáld a ZIP-ből kicsomagolt `.fit` fájlt.
4. Rögzíts egyetlen Garmin-időeltolást és annak bizonytalanságát. Pozitív eltolás későbbre helyezi a Garmin mintáit; az ismeretlen bizonytalanságot hagyd üresen.
5. Ellenőrizd a kieséseket és a lefedettséget, majd töltsd le az eredeti fájlokat, a JSON-riportot és a `timeline.csv`-t tartalmazó ZIP-et.

A publikus API-felvétel nem nyers CSI. A külön UDP-collector a RuView loopback tee-jével azonos hálózati névtérben fusson. A Linuxos segédprogramokat és a kalibrációt kizárólag a dokumentált környezetben, külön operátori utasításra futtasd. A passzív diagnosztikai scriptek jelenleg telepítéshez kötött IP-/interfészszűrőt használnak; használat előtt ellenőrizd ezeket. A `calibrate-ruview.py` mellé a `calibration.py`, a `record-passive-raw.py` mellé a `raw.py` és `pcap.py` modul telepítendő.

## Ellenőrzés

```sh
.venv/bin/python -m pytest -q
npm --prefix web run build
```

A tesztek SYNTHETIC FIT- és UDP-mintákkal ellenőrzik az adatkezelést; élettani pontosságot nem igazolnak. A hiányzó becslés `null`. A riport API-fogadási idő szerinti megfigyelési összehasonlítás, mert a publikus végpont nem közli a becslés ablakvégét és profilazonosítóját.

## Fejlesztés és upstream-frissítés

A fejlesztési repó [Baunt/RuView](https://github.com/Baunt/RuView), az eredeti forrás [ruvnet/RuView](https://github.com/ruvnet/RuView). Az AutoTuner módosításai ebbe a `tools/autotuner` mappába kerülnek.

Az `origin` a saját forkra, az `upstream` az eredeti repóra mutasson. Az upstream változásait merge-del, az AutoTuner commitjait megőrizve kell beemelni. Frissítés előtt ellenőrizd a munkapéldányt; ne írj felül befejezetlen munkát. A merge után futtasd a fenti tesztet és buildet, továbbá a módosult RuView-rész szükséges ellenőrzéseit. Sikertelen ellenőrzés vagy megoldatlan konfliktus esetén ne frissítsd a fork `main` ágát. A forrásfrissítés önmagában nem telepítés a futó szerverre.

## Dokumentumok

- [Mérőállomás: adatút, riport, collector és korlátok](docs/meroallomas.md)
- [A teljes tuner megvalósítási terve](docs/auto-tuner-terv.md)
- [Verzióhoz kötött RuView-forráskutatás](docs/ruview-integration-research.md)
