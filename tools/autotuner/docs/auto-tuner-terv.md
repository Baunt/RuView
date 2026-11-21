# RuView AutoTuner – megvalósítási terv

Dátum: 2026-11-14. Állapot: tervezett rendszer, megvalósítás és hardvermérés nélkül.

**2026-11-15-i fejlesztési frissítés:** elkészült az első helyi mérőállomás API-felvétellel, FIT-importtal, rögzített időillesztéssel, megfigyelési riporttal és külön nyers UDP-collectorral. Az alábbi dokumentum a teljes, későbbi tuner terve; az aktuálisan megvalósított képességeket és az éles integráció hátralévő lépéseit a [mérőállomás útmutatója](meroallomas.md) írja le. Paraméterkeresés, új engine és pontosságjavulás még nincs igazolva.

## 1. Döntés és cél

**Helyben futó, referencia alapján tanuló paraméterhangolót építsünk, amely először a RuView szerveres pulzusbecslését javítja.** Ugyanez a felépítés később a légzésre is kiterjeszthető.

A cél pontosabb légzés- és pulzusbecslés. Az első tervezett referencia Garmin FIT-pulzusadat; pulzoximéter külön kontrollként használható. Légzési referencia nélkül légzési pontosságjavulás nem értékelhető. Ezért az első változat a Garmin pulzusértékeihez viszonyított eltérést optimalizálja, a pulzoximétert ellenőrző forrásként használja. A légzésnél jelminőséget és bizonytalanságot értékel, igazolt pontosságjavulást nem állít.

A Garmin referenciaeszköz, nem hibátlan élettani alapigazság. A jelentés neve ezért „eltérés a Garmin-referenciától”; a pulzoximéterrel való egyezés külön eredmény. Az első kiadás egy személy nyugalmi, ülő/fekvő mérésére készül. Orvosi döntés, apnoéfelismerés, SpO₂-becslés, több ember vitals-szétválasztása és mozgás közbeni pontos pulzusmérés nem első kiadásbeli cél.

**Termékígéret:** a felhasználó készít néhány vezetett mérést, az alkalmazás összehasonlítja a beállításokat, megmutatja a javulást és bizonytalanságát, majd ellenőrzötten aktivál egy helyiséghez kötött profilt. Ha nincs bizonyíthatóan jobb jelölt, megtartja az alapbeállítást.

## 2. A rendelkezésre álló alapok

A vizsgált RuView-forrás commitja `441349137008ce30ec0f147ccba0d4aaf65ddee5`. A részletes bizonyítékok a [forrásellenőrzésben](ruview-integration-research.md) találhatók.

| Terület | Meglévő képesség | Szükséges fejlesztés |
|---|---|---|
| Jelfeldolgozás | Rust `VitalSignDetector`, node-onkénti feldolgozás és simítás | Konfigurálható vitals profil és közös, visszajátszható feldolgozómag |
| Adatgyűjtés | Loopback UDP tee teljes datagramokkal | Nyers rögzítő, időzítési napló, ellenőrzött session-formátum |
| Kalibráció | Szobához/node-okhoz kötött explicit kalibráció | Vezetett folyamat, állapotellenőrzés, profilkapcsolat |
| Megjelenítés | REST/WS vitals és jelenlétadatok | Egyértelmű `server_csi`/`edge` eredet, profilazonosító, adatkor |
| Paraméterírás | Nem találtam általános vitals runtime profilfelületet | Validálás, ideiglenes aktiválás, visszaolvasás, véglegesítés, rollback |
| Referencia | Garmin FIT-export és pulzusközvetítés dokumentált | FIT-adapter elsőként; élő adapter később |

Az ESP32 firmware saját vitals algoritmusa és a szerver algoritmusa eltér. **Az első verzió a szerveres útvonalat hangolja.** A kiválasztott node nyers CSI-adataiból ugyanaz az új feldolgozómag dolgozik offline és élesben. Az edge-csomag nem írhatja felül ezt az eredményt más eredetű értékkel; a két útvonal külön felirattal maradhat látható.

Az amplitúdót tartalmazó WS/JSONL-felvétel önmagában nem elegendő a jelenlegi, fázist is használó pulzusbecslő teljes újraértékeléséhez. Ha csak edge BPM-adat vagy amplitúdó érhető el, a rendszer összehasonlító megfigyelést végez; a teljes pulzushangolás állapota `raw_capture_required`.

## 3. Felhasználói folyamat

1. **Csatlakozás.** RuView-végpont, verzió, élő forrás, kiválasztott node, adatfrissesség és elérhető nyers adat ellenőrzése. A szimulált adat külön jelölést kap, fizikai pontossági értékelésbe nem kerül.
2. **Referencia.** Egy óra-tevékenység eredeti FIT-fájljának importja. Élő pulzusközvetítés későbbi kényelmi bővítés. A pulzoximéterhez időbélyeges kézi kontrollpontok is megadhatók, ha nincs export.
3. **Előkészítés.** Szenzorelhelyezés rögzítése, explicit üres-szoba kalibráció, majd egyetlen személy mérése. A kalibráció végét a szerver feltételei döntik el.
4. **Vezetett felvétel.** Kezdetben 15–25 perces pilot: külön nyugalmi ülő és fekvő blokkok, több orientáció, rövid természetes helyzetváltoztatások, üres szoba. A mozgásjelölés esemény, nem elvárt vitals-pontosság.
5. **Keresés.** Ugyanazokat a rögzített adatokat legfeljebb 40 profiljelölttel kiértékeli. A felhasználó közben nem ismétli meg negyvenszer a mérést.
6. **Független ellenőrzés.** Új időpontban felvett, a keresés elől elzárt méréssorozat igazolja vagy elutasítja a jelöltet.
7. **Alkalmazás.** Profilkülönbség, pulzushiba, lefedettség, késleltetés és korlátok megjelenítése után ideiglenes aktiválás, ellenőrzés, majd véglegesítés. Egyetlen „Előző profil visszaállítása” művelet.

A pilot az adatút és a megvalósíthatóság vizsgálata. Nem helyettesíti a későbbi többalkalmas ellenőrzést.

### Garmin és pulzoximéter

Az első referencia-adapter eredeti FIT-fájlból olvassa a `timestamp` és `heart_rate` rekordokat. Az eredeti felvételt változatlanul megőrzi, kezeli a FIT időábrázolását és hiányzó értékeit, ellenőrzi az időrendet. A napi átlag vagy az aktivitások összesítő CSV-je nem elég. A Garmin dokumentál `Every Second` rögzítést; az importőr a fájlban található tényleges mintaközt használja, nem feltételez garantált másodpercenkénti friss pulzusmérést. [Garmin adatrögzítés](https://www8.garmin.com/manuals/webhelp/GUID-31D23DBB-57C2-4DF7-A0C9-8D1A00AB4BE7/EN-US/GUID-1500E73F-F386-49AF-A542-25D4B1655A08.html).

A pulzoximéter típusától függően USB/Bluetooth/fájlos adapter vagy kézi kontrollpont működik. A kijelzőn látható **pulzusérték** a kontroll; a SpO₂-százalék nem légzésszám-referencia. Egy-egy kézi pont csak az adott stabil szakaszt ellenőrzi, nem ad folyamatos referenciafolyamot. A két eszköz eltérése külön jelölést kap; a tuner nem átlagolja automatikusan őket, és nem választja azt, amelyikhez a RuView éppen közelebb van.

### A képernyők

| Nézet | Lényegi tartalom |
|---|---|
| Állapot | Nyers adat rendelkezésre áll-e; node és profil; referencia; kalibráció; tényleges mintavétel |
| Mérés | Aktuális forgatókönyv, hátralévő idő, eseményjelölés, óra/FIT-hozzárendelés |
| Összehasonlítás | Garmin, alap-RuView és jelölt ugyanazon időtengelyen; hibák és hiányok láthatóan |
| Profil | Mit változtat, melyik helyiségre és node-ra érvényes, milyen adaton ellenőriztük |

„Nincs megbízható becslés”, „Bemelegedés”, „Mozgás”, „Hiányzó referencia”, „Több személy” és „Elavult adat” külön állapot. Korábbi értéket nem tüntetünk fel friss mérésként.

## 4. Felépítés és technológia

Adatút: **ESP32 → RuView nyers adatmásolat → rögzítés → azonos Rust becslő több profillal → Garmin-összehasonlítás → keresés → ellenőrzött profil → RuView.**

| Modul | Kicsi külső interface | Amit az implementation elrejt |
|---|---|---|
| `Capture` | `record(session_spec) → capture_id` | UDP-formátum, I/Q, sorszámok, időzítés, csomagvesztés, fájllezárás |
| `Reference` | `read(source) → reference_track` | FIT és később élő eszközadapter, egységek, időábrázolás, minőség |
| `VitalsEngine` | `new(profile)`, `push(observation) → estimate`, `reset(reason)` | Puffer, szűrés, csúcskeresés, simítás, minőségi állapot |
| `TuningSession` | `run(spec) → proposal`, `status(id)`, `cancel(id)` | Adatszétválasztás, TPE, próbakeret, mérés, bizonytalanság, jelentés |
| `ProfileDeployment` | `activate(proposal, expected_revision) → receipt`, `revert(receipt)` | Kompatibilitás, snapshot, írás, readback, időkorlát, visszaállítás |

A **seam** a becslő bemenete és a verziózott profil: az offline és az élő adapter ugyanazt a Rust implementationt használja. A referencia kizárólag az értékelő bemenete; az éles `VitalsEngine` nem kap Garmin-adatot, így működés közben nem másolhatja le a referenciát.

**Javasolt stack:** Python + Optuna a kereséshez és a feladatvezérléshez; Rust a RuView-val közös becslőhöz; FastAPI és kis React/TypeScript felület a helyi kezelőhöz; SQLite a sessionök, próbák és profilok metaadataihoz. A nyers felvételek külön, tömörített, ellenőrzőösszeggel ellátott fájlokban maradnak. Elsőként subprocess/JSONL interface a Rust evaluatorhoz; Python/Rust FFI csak akkor, ha mérés szerint szükséges.

Az UDP tee loopback korlátozása miatt a collector a RuView-val **azonos hálózati névtérben** fut. Dockerben ehhez tudatos namespace-megosztás vagy konténeren belüli collector kell; két sima, közös Docker-hálózaton lévő konténer loopbackje különbözik. Az optimalizáló külön processz, korlátozott CPU-val; az éles érzékelési ciklust nem várakoztathatja.

Az első verzió helyi, egyfelhasználós alkalmazás. Nincs szükség felhőre vagy LLM-re a kereséshez. A későbbi LLM a már kiszámolt eredményt magyarázhatja; nyers egészség- és CSI-adatot alapból nem kap, paramétert közvetlenül nem ír.

## 5. Adatok, időzítés és bizonyíték

Minden `CaptureSession` tartalmazza a forrás/szerver/firmware verziót, node-azonosságot, csatornát, CSI-rácsot, helyiség- és elhelyezésazonosítót, kalibrációs receiptet, aktív profilt és a forgatókönyv jelöléseit. A személy helyi, álneves azonosítót kap; egyetlen személy adataiból nem állítunk általánosíthatóságot.

A rögzítő változatlan datagramot, host monotonic érkezési időt, UTC-illesztést és elérhető szinkroncsomagokat ment. A nyers keret sorszáma segít azonosítani a duplikációt, kiesést és újraindulást. A csatorna, PPDU/rács vagy node újraindulása új szakaszt indít; különböző rádiós források mintái nem kerülnek egy folytonos fázispufferbe.

A `ReferenceTrack` mezői: forrás, mérési idő, pulzus, minőségi jelölés, mintaköz, ismert vagy ismeretlen eszközkésés, időillesztési bizonytalanság. A kézi pulzoximéter-pontnál az időpont és annak bizonytalansága is kötelező.

**Időillesztés:** a collector és az óra időalapját előre összehangoljuk; a mérés kezdetén és végén közös, naplózott esemény segít ellenőrizni az offsetet/driftet. FIT-importnál a maradó eltérést és bizonytalanságát rögzítjük. Ha az óra belső simításának késése ismeretlen, a gyors változások időzítése bizonytalan eredmény, nem precíz késleltetésmérés.

A keresés nem optimalizálhatja a referencia időeltolását. Egy előre rögzített illesztést használ minden jelöltnél. A kijelzés időpontjában elérhető referenciaértékhez mért eltérés az elsődleges; az ablak közepéhez rendelt, utólagos összehasonlítás csak külön diagnosztika. Nincs jelöltenkénti görbeeltolás, amely eltüntetné az algoritmus késését.

A tényleges mintavételi frekvencia mért mennyiség, nem kereshető paraméter. A késői, hiányzó és szabálytalan csomagokat jelölni kell. Az interpoláció korlátozott, szabálya előre rögzített; hosszú rést nem töltünk ki. Az érvénytelen időzítésből nem készül érvényes pulzusablak. A felvétel és a reference import minőségi szabályai minden profilra azonosak.

`Estimate` mezők: `observed_until`, `emitted_at`, `window_seconds`, `source`, `node_id`, `profile_id`, `hr_bpm?`, `br_rpm?`, `quality_score`, `status`, `reason`. A `quality_score` nem valószínűségként kalibrált pontossági állítás. Az ismeretlen érték `null`, nem 0 vagy korlátokra szorított pulzus.

## 6. Mit hangol az AI?

### Elsőként kis keresési tér

Az első referencia-alapú keresés a **pulzus mérési ablakát és simításának időállandóját** hangolja. Az eredeti RuView-profil külön baseline. Minden változás szerveroldali, új `VitalsProfile` támogatást igényel.

| Paraméter | Tervezett kezdeti keresési tartomány | Hatás |
|---|---|---|
| `hr.window_seconds` | 10, 15, 20, 30 s | Felbontás, zaj és követési késés |
| `hr.smoothing_tau_seconds` | 0,5–8 s | Zajelnyomás és változáskövetés |
| `hr.robust_window_seconds` | 0, 1, 2, 3 s | Rövid kiugrások szűrése |

Ezek **mérnöki kiinduló tartományok**, nem bizonyítottan optimális értékek. A keresés csak a pilot után véglegesített, verziózott tartományt használja. Az EMA időalapú: `alpha = 1 - exp(-dt/tau)`; új érvényes becslés hiányában a rendszer nem frissíti mesterségesen az időbélyeget. Az ugráskorlátok és holtsávok kezdetben rögzített policy részei, külön teszttel a valódi, tartós változások utáni újraalkalmazkodásra.

A légzési ág profilja az első pulzuskeresés alatt változatlan. Ha később rendelkezésre áll időbélyeges légzési referencia, külön keresés nyílik például 30/45/60 s ablakokra és időalapú simításra. A sávhatárok és a minimális megfigyelési idő összefüggését validálni kell; a rövid jel nullaértékekkel való FFT-kiegészítése nem pótolja a hiányzó megfigyelést.

A kezdeti érvényességi tartomány a vizsgált becslő pulzussávjához kötött, nagyjából 40–120 ütés/perc. A keresés nem szűkítheti a sávot az aktuális Garmin-érték köré, és nem szoríthatja a becslést a referenciaszámhoz. A tartományon kívüli referencia külön, nem támogatott esetként jelenik meg a teljes mérési idő kimutatásában. A szélesebb pulzustartomány támogatása külön algoritmus- és validációs feladat.

### Következő bővítés, ha a pilot indokolja

- Alvivő-választás és minőség szerinti súlyozás a nyers I/Q-ból.
- Fázis-előkészítési változatok és FFT/autokorreláció közötti választás.
- Légzési felharmonikusok felismerése; pusztán a légzés többszöröséhez közeli valódi pulzust nem szabad automatikusan kitiltani.
- Referencia alapján tanult megbízhatósági modell, amely a hibás ablakokat ismeri fel. Külön tanító/validációs adatokkal, lefedettségi korláttal.

Ezek új algoritmusváltozatok, nem már meglévő runtime kapcsolók. Előbb implementálni és a baseline-nal összehasonlítani kell őket. Firmware-paraméterek hangolása külön későbbi fejlesztés, hardveres validációval.

### A keresés menete

1. Rögzített seed, baseline és 8–10 kezdeti paraméterpróba.
2. Legfeljebb 40 összes próbán belül Optuna/TPE választ további jelölteket az eddigi hibákból.
3. Minden próbán azonos sessionök, azonos bemelegedési policy és azonos referenciaillesztés.
4. A legjobb jelölt külön végső mérésen ellenőrizhető; a tesztadat soha nem kerül vissza a keresésbe.
5. Elegendő adat vagy javulás hiányában `no_verified_improvement` az eredmény.

Az [Optuna TPE](https://optuna.readthedocs.io/en/stable/reference/samplers/generated/optuna.samplers.TPESampler.html) itt az AI-rész: a mérési visszajelzésből tanuló paraméterkeresés. A mintaszám és próbakeret konfigurálható; az alapértékek nem futásidő-ígéretek.

## 7. Mitől számít jobbnak?

### Mérőszámok

| Mérőszám | Definíció és szerep |
|---|---|
| Pulzus MAE | Érvényes kiadott értékek átlagos abszolút eltérése a Garmin-referenciától, ütés/percben |
| P95 abszolút eltérés és bias | Nagy hibák, illetve szisztematikus alá-/fölébecslés |
| Lefedettség | Friss, érvényes becslések aránya az összes előre kijelölt, érvényes referenciájú nyugalmi időpontból |
| Kezdeti várakozás | A mérés vagy megszakadás utáni első érvényes becslésig eltelt idő |
| Követési késés | Referenciában megfigyelhető tartós változás után a becslés követése; bizonytalan referenciaidőzítéssel külön jelölve |
| Hibás kibocsátás | Szám megjelenítése üres szobánál, több személynél, megszakadt vagy elavult adatra |
| Légzési jelminőség | Folytonosság, spektrális elkülönülés és rendelkezésre állás; referencia nélkül nem légzési MAE |

A referenciaérvényességet és a nyugalmi blokkokat a keresés előtt, a RuView-jelöltektől függetlenül rögzítjük. A jelölt által eldobott ablakok a lefedettség nevezőjében maradnak. A teljes mérési időre és a feltételeket teljesítő részre is közlünk arányt, így a hibás jelenlétkapu nem rejtheti el a mérési hiányt.

### Konkrét optimalizálási cél

Az összes előre kijelölt, érvényes referencia-időponton:

```text
loss(t) = min(abs(RuView_HR(t) - Garmin_HR(t)) / 5, 4), ha van friss becslés
loss(t) = 4, ha nincs friss becslés
J = a sessionönkénti átlagos loss értékek átlaga
```

A sessionök azonos súlyt kapnak, így egy hosszú ülő felvétel nem nyomja el az összes többi helyzetet. Az 5 ütés/perc skála és a hiány büntetése kezdeti, rögzítendő mérnöki döntés. Külön feltétel a lefedettség és a késleltetés, ezért puszta kisimítással vagy az összes nehéz minta eldobásával nem nyerhető meg a keresés.

Kezdeti elfogadási célszámok, **még nem mért eredmények**:

- Nyugalmi Garmin-eltérés: MAE ≤ 5 ütés/perc, P95 ≤ 10 ütés/perc.
- Lefedettség ≥ 80%, a baseline-hoz képest legfeljebb 2 százalékpontos csökkenéssel.
- Új mérés utáni pulzusbecslés ≤ 35 s; tartós változás követése tervezetten ≤ 15 s, ha a referencia időzítése ehhez elég pontos.
- A hibás kibocsátásra és stale állapotra vonatkozó ellenőrzések minden esetben teljesülnek; a minimális minőségi és kalibrációs kapuk nem csökkenthetők.
- `J` legalább 10%-kal csökken, és a független sessionök párosított különbségének 95%-os bootstrap intervalluma is javulást jelez. Kis mintán csak pilot-eredmény adható, automatikus „igazolt” minősítés nem.

Ha a baseline már nagyon jó és a különbség kicsi, a baseline marad. Az elfogadási szabályokat a pilot után, a végső teszt felnyitása előtt rögzítjük.

A riport két további egyszerű kontrollt is megmutat: a tanító alkalmak Garmin-átlagát állandóan kiadó becslőt és az utolsó érvényes RuView-értéket tartó becslőt. Ha a jelölt csak egy közel állandó nyugalmi pulzust talál el, abból nem következik változáskövetési képesség. Ehhez természetesen eltérő pulzusú, külön alkalmak és azonosítható változások kellenek. A közös feldolgozómag kialakításából eredő javulást külön közöljük az AI-paraméterkeresés többletétől: legacy RuView → rögzített új alap-profil → keresett profil.

### Adatszétválasztás

Tanító/kereső, validációs és végső tesztadat **külön mérési alkalmakból** áll. Nem egymást követő, átfedő ablakokat osztunk szét véletlenszerűen. Normalizálás, alvivőválasztás és esetleges minőségmodell csak a tanító részhez férhet hozzá. Az állapotot minden független felvétel elején alaphelyzetbe állítjuk, a bemelegedés valós ideje külön metrika.

A fejlesztési validáción kiválasztott profilt a végső teszt egyszer minősíti. Elutasítás után az új kereséshez új érintetlen tesztkészlet kell. A kezdeti személyes profil több külön alkalomra és több testhelyzetre támaszkodjon; más személyre/szobára vonatkozó állítás külön adatok nélkül nincs.

## 8. Alkalmazás, visszaállítás és tartós működés

A jelenlegi RuView publikus vitals kapuit meg kell őrizni: friss explicit kalibráció, egyetlen személy, megfelelő minőség. Ezek alkalmazhatósági feltételek, nem optimalizálható jutalomküszöbök. A szoba újrakalibrálása nem igazol Garminhoz mért pontosságot, a Garmin-illesztés pedig nem helyettesíti a szobakalibrációt.

A telepítés állapotai: `proposed → validated → staged → active_trial → active`, hiba esetén `reverted` vagy `needs_attention`.

Aktiválás előtt a modul ellenőrzi a szerver/engine verziót, room/node/CSI-rács kötést, profil hash-t, aktuális konfigurációs revisiont és az ellenőrzési jelentést. Egyszerre egy író lehet. Az előző profil tartós mentése az új írás előfeltétele.

Az ideiglenes profilhoz szerveroldali lejárat tartozik. A RuView visszaolvasható alkalmazási receiptet és a kimeneteken profilazonosítót ad. Az új profil saját puffere a háttérben melegszik be, addig az előző marad aktív; ha ez erőforrás miatt nem lehetséges, a váltás átmeneti `warming_up` állapotot jelez. A régi és új profil mintáit nem keverjük.

Sikeres health/readback és rövid működési próba után véglegesítés. Az élő próba referencia nélkül csak a működést és az adatminőséget igazolja; új pontosságjavulást nem. Kommunikációs hiba, profilazonossági eltérés, jelentős csomagvesztés vagy az alkalmazási feltételek megszűnése esetén visszaállítás. Érvénytelen kalibráció esetén a régi profil sem jogosult számot kiadni.

A rollback az előző tartós profilt is visszaállítja, és ezt readback igazolja. Újrainduláskor befejezetlen tranzakcióból az utolsó véglegesített profil töltődik be. Sikertelen rollbacknél `needs_attention`, numerikus eredmény visszatartása és érthető hibaüzenet szükséges; nincs sikernek jelzett félkész állapot.

A driftfigyelés eleinte újramérést javasol: megváltozott szenzorelhelyezés, csatorna, firmware, tartós jelminőségromlás. Referencia nélkül nem tanítja saját korábbi pulzusbecslését címkének. Profilonként rögzül, melyik helyiségre, személyes mérési tartományra és szenzorra van bizonyíték.

## 9. Fejlesztési sorrend és ellenőrzés

| Fázis | Eredmény | Kilépési feltétel |
|---|---|---|
| 0. Kompatibilitás | Telepített verzió, node, raw/edge forrás, kalibráció, Garmin mintafájl felmérése | Valódi I/Q-adat és használható pulzusreferencia igazolt |
| 1. Mérőállomás | Nyers collector, FIT-import, szinkron, eseményjelölés, alapállapot-riport | Azonos felvétel reprodukálható értékelése; időbizonytalanság és hiányok láthatók |
| 2. Közös becslő | RuView-val közös Rust mag, `VitalsProfile`, offline/élő adapter | A legacy profil reprodukálja a rögzített baseline-t; eltérések magyarázottak |
| 3. AI-keresés | Kis paramétertér, Optuna, fix próbakeret, független értékelő | Nem jutalmazza a lefagyást, a hiányt vagy az adatkeverést |
| 4. Alkalmazás | Profilreceipt, revision, próbaidő, readback, rollback | Hibainjektálással ellenőrzött helyreállás és valódi eszközös próba |
| 5. Bővítés | Légzési referencia, jobb alvivőválasztás, élő Garmin-kapcsolat | Külön előny igazolt a korábbi verzióhoz képest |

Tervezési becslés: az első négy megvalósítási fázis egy fejlesztővel körülbelül **3–5 hét**, ha a nyers adatút és a referencia azonnal használható. Az ismételt hardvermérések naptári ideje, firmware-problémák és az élő pulzoximéter-integráció ezt növelhetik. Ez nem vállalás; a 0–1. fázis után pontosítható.

### A szükséges RuView-módosítások

1. `vital_signs.rs` és a vitals simítás közös, profilból épülő modullá alakítása; a jelenlegi viselkedés baseline-ként megőrzése.
2. Verziózott profillekérdezés, validálás és tranzakciós alkalmazás. Például `/api/v1/tuning/...` alatt; ezek **javasolt új végpontok**, jelenleg nem létező integrációként kezelendők.
3. Node/profil/engine eredet és mérési frissesség publikálása; a server és edge vitals szétválasztása a kijelölt hangolt kimeneten.
4. A collectorhoz a meglévő nyers tee használata. A CLI-beállítás felvétele a telepített rendszeren újraindítást igényelhet, ezért nem része az élő paraméterkeresési ciklusnak.

### Lényegi tesztek

- Analitikus szinuszok és felharmonikusok: ismert frekvencia, zaj, amplitúdó/fázis, eltérő mintavételi ütem. Ez szintetikus algoritmusteszt, nem hardveres pontossági bizonyíték.
- Valós felvétel: az offline és az élő adapter ugyanazon rendezett bemenetre azonos profillal azonos eredményt ad, dokumentált numerikus toleranciával és ugyanazzal a virtuális órával.
- Csomagkiesés, sorrendcsere, duplikáció, újraindulás, clock drift és csatornaváltás: nincs hamisan friss érték vagy kevert puffer.
- Valódi tartós változást a kiugrásszűrő nem fagyaszt be a korábbi pulzusra; megakadó kimenet rontja a pontszámot.
- Csak könnyű ablakokat megtartó vagy mindent eldobó profil nem lehet nyertes.
- Hiányos FIT, hibás időzóna, másodpercenként ismételt de nem új mérés, megszakadó referencia kezelése.
- Profilírás közbeni processzkilépés, readback-hiba, revision-konfliktus és újraindulás: ellenőrizhető visszaállás.
- Üres szoba/több személy/stale kalibráció: a profil nem kerüli meg a publikációs feltételeket.

## 10. Nyitott részletek és induló feladat

Már eldőlt: vitals-fókusz; Garmin Instinct 2 Solar + pulzoximéter; nincs légzési referencia; helyi, szerveroldali hangolás; nyers CSI-visszajátszás; független teszt és rollback.

Megvalósításkor tisztázandó: aktuális RuView build, ESP32 hardver és firmware, node-ok száma/elhelyezése, a pulzoximéter pontos típusa/exportja, egy valódi Garmin FIT-fájl, a mérési időillesztés elérhető pontossága. Ezek nem akadályozzák a tervet, de a 0. fázis kilépési feltételeihez kellenek.

**Első konkrét fejlesztési feladat:** készítsünk egy helyi mérőállomást, amely ugyanarra az időtengelyre teszi a Garmin pulzusát, a RuView jelenlegi pulzusbecslését, a jelminőséget és a hiányzó becsléseket. A keresés csak akkor induljon, ha ez már reprodukálhatóan megmutatja, milyen hibát akarunk csökkenteni.
