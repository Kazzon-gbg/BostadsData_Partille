# BostadsData_Partille
Tar fram bostadsdata from 2023-01-01 till "ToDay"

# Hämta bostadsdata för Partille kommun

Detta script används för att hämta, städa och strukturera bostadsdata för
Partille kommun, inklusive Sävedalen. Resultatet används som underlag till mitt
examensarbete i **Python for AI**, där en maskininlärningsmodell tränas för att
prediktera bostäders slutpris.

Det här projektet är ett separat förarbete till själva AI-/modellprojektet.
Syftet är att skapa en ren CSV-fil som kan användas som input till modellen.

## Syfte

Syftet med scriptet är att:

- hämta slutpriser för bostäder i Partille kommun
- filtrera fram bostadstyperna villa, radhus, kedjehus och parhus
- hämta data från 2023-01-01 fram till dagens datum
- rensa bort felaktiga eller dubbla rader
- beräkna utgångspris där det går
- skapa geografiska features baserat på område
- spara en städad CSV-fil som kan användas i maskininlärningsprojektet

## Källa och ansvar

Scriptet hämtar publikt synliga slutprisuppgifter från Boolis slutprissidor för
Partille kommun.

Använd scriptet ansvarsfullt:

- kör inte scriptet för ofta
- använd låg hastighet mellan sidladdningar
- kontrollera alltid källans användarvillkor
- använd datan för utbildning och analys

## Output-filer

Scriptet skapar två filer i mappen `data/`:

```text
data/partille_housing_real_2023_today.csv
data/partille_housing_real_2023_today_raw_debug.csv
```

### `partille_housing_real_2023_today.csv`

Detta är den städade ML-filen som används i examensarbetets modellträning.

### `partille_housing_real_2023_today_raw_debug.csv`

Detta är en råfil för felsökning. Den sparas för att kunna kontrollera vad som
hämtades innan städning och deduplicering.

## Period och område

Scriptet hämtar data från:

```text
2023-01-01 till dagens datum
```

Området är:

```text
Partille kommun, inklusive Sävedalen
```

I Booli-länken används `areaIds=268`, vilket motsvarar Partille kommun i detta
projekt.

## Bostadstyper

Följande bostadstyper tas med:

- Villa
- Radhus
- Kedjehus
- Parhus

I scriptet används Boolis filter:

```text
objectType=Villa,Kedjehus-Parhus-Radhus
```

## Installation

Installera paketen som behövs:

```bash
pip install requests beautifulsoup4 pandas
```

Scriptet använder bland annat:

- `requests` för att hämta webbsidor
- `BeautifulSoup` för att läsa HTML
- `pandas` för att strukturera och spara data
- `re` för texttolkning med reguljära uttryck
- `datetime` för datumfiltrering

## Körning

Kör scriptet från projektmappen:

```bash
python hamta_partille_bostadsdata_booli_v6_geo_features.py
```

Efter körning skapas eller uppdateras CSV-filerna i `data/`.

## Arbetsflöde i scriptet

### 1. Hämtar listvyer från Booli

Scriptet går igenom sidor med slutpriser från Booli. Varje sida hämtas med
`requests`.

För att minska risken för avbrutna anslutningar finns:

- timeout
- retry-funktion
- slumpmässig paus mellan sidor
- säkerhetsstopp med max antal sidor

### 2. Tolkar HTML och text

HTML-sidan läses med BeautifulSoup. Därefter plockas text ut från sidan.

Scriptet använder reguljära uttryck för att hitta:

- datum
- slutpris
- bostadstyp
- boarea
- biarea
- antal rum
- tomtarea
- kr/m²
- prisförändring i procent
- försäljningstyp, till exempel slutpris eller lagfart

### 3. Delar upp sidan i bostadsrader

Boolis listvy kan innehålla mycket text på samma sida. Scriptet försöker därför
klippa ut varje faktisk försäljningsrad separat.

Detta är viktigt eftersom tidigare versioner kunde få med skräptext som:

```text
Gå till innehåll Gå till sök...
```

### 4. Beräknar utgångspris

Om Booli visar procentuell skillnad mellan utgångspris och slutpris kan scriptet
beräkna ett uppskattat utgångspris.

Formeln är:

```text
utgångspris = slutpris / (1 + procentuell förändring / 100)
```

Exempel:

```text
Slutpris: 6 600 000 kr
Prisförändring: +4,8 %
Beräknat utgångspris: cirka 6 298 000 kr
```

Alla bostäder har inte procentuell prisförändring. Därför kommer vissa rader
sakna utgångspris.

### 5. Städar adresser och tar bort skräprader

Scriptet tar bort rader som troligen inte är riktiga bostadsrader.

Exempel på sådant som filtreras bort:

- navigeringstext
- söktexter
- sidrubriker
- för långa adresser
- tomma eller orimligt korta adresser

### 6. Deduplicerar rader

Samma bostad kan ibland förekomma flera gånger, exempelvis både som slutpris och
lagfart. Scriptet försöker därför ta bort dubbletter baserat på:

- adress
- datum
- slutpris
- boarea
- bostadstyp
- antal rum
- tomtarea

Det gör datasetet mer lämpligt för maskininlärning.

### 7. Skapar geografiska features

Scriptet lägger till ungefärliga geografiska features baserat på `area_name` och
adress.

Följande kolumner skapas:

```text
approximate_latitude
approximate_longitude
area_group
distance_to_partille_center_km
distance_to_gothenburg_center_km
distance_to_savedalen_center_km
```

Dessa koordinater är inte exakta per adress. De bygger på ungefärliga
centrumkoordinater för områden som exempelvis Sävedalen, Öjersjö, Furulund,
Kåhög, Jonsered och Lexby.

Syftet är att ge modellen bättre information om läge än bara ett områdesnamn.

## Viktiga kolumner i den städade CSV-filen

Den städade filen innehåller bland annat:

```text
address
property_type
area_name
municipality
is_savedalen
sale_type
sold_date
year
month
final_price_sek
asking_price_sek
price_change_sek
price_change_percent
living_area_m2
extra_area_m2
rooms
plot_area_m2
price_per_m2
bid_change_percent
source_url
approximate_latitude
approximate_longitude
area_group
distance_to_partille_center_km
distance_to_gothenburg_center_km
distance_to_savedalen_center_km
price_outlier_flag
area_outlier_flag
plot_outlier_flag
```

## Outlier-flaggor

Scriptet markerar vissa rader med flaggor:

```text
price_outlier_flag
area_outlier_flag
plot_outlier_flag
```

Dessa används för att kunna kontrollera avvikande rader, till exempel:

- mycket lågt eller mycket högt slutpris
- ovanligt liten eller stor boarea
- ovanligt liten eller stor tomtarea

Scriptet markerar alltså misstänkta rader i stället för att automatiskt ta bort
allt.

## Varför geografiska features används

Läge är en av de viktigaste faktorerna för bostadspris. Samtidigt är exakt
adress inte alltid lämplig som modellfeature eftersom det kan skapa
överanpassning.

Därför används ungefärliga lägesfeatures i stället:

- ungefärligt område
- avstånd till Partille centrum
- avstånd till Göteborg centrum
- avstånd till Sävedalen centrum

Det ger modellen lägesinformation utan att den tränas direkt på varje enskild
adress.

## Begränsningar

Scriptet har flera begränsningar:

- HTML-strukturen på Booli kan ändras.
- Alla bostäder har inte komplett information.
- Utgångspris kan bara beräknas när procentuell prisförändring finns.
- Koordinaterna är ungefärliga och inte exakta per adress.
- Scriptet hämtar inte byggår, energiklass eller driftkostnad i denna version.
- Datan behöver kontrolleras innan den används i modellträning.

## Relation till examensarbetet

Detta script ingår som ett separat förarbete till examensarbetet.

Själva examensarbetet fokuserar på:

- att läsa in den färdiga CSV-filen
- analysera datan
- skapa features
- träna regressionsmodeller
- jämföra modellresultat
- skriva resultatrapport

Detta script ansvarar endast för att skapa datasetet:

```text
data/partille_housing_real_2023_today.csv
```

## Kort slutsats

Scriptet skapar en användbar och städad dataset med verkliga bostadsförsäljningar
för Partille kommun. Det gör det möjligt att träna en maskininlärningsmodell på
svensk bostadsdata i stället för ett generiskt testdataset.

Samtidigt är det viktigt att förstå att datan inte är perfekt. Resultatet bör
därför alltid kontrolleras och användas med försiktighet.