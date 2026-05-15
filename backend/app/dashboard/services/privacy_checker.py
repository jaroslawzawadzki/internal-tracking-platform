import asyncio
import re
from playwright.async_api import async_playwright

class PrivacyAuditService:
    @staticmethod
    async def run_audit(url):
        async with async_playwright() as p:
            # Docker setup: headless=True jest wymagane
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = await context.new_page()
            
            audit_result = {
                "url": url,
                "is_shopify": False,
                "consent_leaks": False,
                "details": {}
            }

            try:
                # Wchodzimy na stronę i czekamy na skrypty
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(3000)
                
                # Kluczowy test: sprawdzamy obiekty Shopify i datalayer
                data = await page.evaluate("""() => {
                    const shopifyObj = window.Shopify || {};
                    const cp = shopifyObj.customerPrivacy;
                    const dl = window.dataLayer || [];
                    
                    const hasGrantedInDl = dl.some(entry => 
                        entry[0] === 'consent' && 
                        entry[1] === 'default' && 
                        Object.values(entry[2] || {}).includes('granted')
                    );

                    return {
                        exists: typeof window.Shopify !== 'undefined',
                        marketingAllowed: cp ? cp.marketingAllowed() : null,
                        visitorConsent: cp ? cp.currentVisitorConsent() : null,
                        dataLayerGranted: hasGrantedInDl
                    };
                }""")

                if data['exists']:
                    audit_result["is_shopify"] = True
                    audit_result["details"] = data
                    
                    # Sprawdzamy wyciek zgody
                    marketing_val = data['visitorConsent'].get('marketing') if data['visitorConsent'] else ''
                    if data['dataLayerGranted'] and (not data['visitorConsent'] or marketing_val == ''):
                        audit_result["consent_leaks"] = True

            except Exception as e:
                audit_result["error"] = str(e)
            
            finally:
                await browser.close()
                
            return audit_result

    @staticmethod
    async def run_batch_from_text():
        # Wklejaj tutaj śmiało nawet 700 linii
        raw_text = """
        fightco.co.uk
pvmarkt.shop
graf-dichtungen.de
hofa-college.de
hofa-plugins. de
hofa-akustik.de
hofa-akustik.de
unchainedrobotics.de
meco-akademie.de
faircouture.com
edeka-suedwest.de
petrhein.de
sanetta.de
diebarista.com
emsraum.de
climaqx.de
bandenwerbungpro.de
hopser-funfood.de
design4kids.de
connect-pmu.de
fcb-basketball.de
fast-forward-fashion.de
drachenfels-design.de | https:
mintyandmarla.de
tauchshop-online.de
licht-zubehoer.de
bergische-wohnmobile.de
camper-tom.de 
waagen-muetze.de
heybee.com
tebo.ag
keyed.de
dielendealer.de
finqaliving.com
shop.purway.de, https:
zapf-garagen.de
wingwheels.com
bernardinos.com
myvelo.de
nextlevel-canvas.de
toniegreen.com
ferryxpress.ch
eglo.com
cool-time.com
emmi-nail.de
hauptstadtkoffer.de
filamentworld.de
eliotfurniture.com
bresser.de
tibu-shop.de
powerflex-deutschland.de, www.zimmermann-brems
equinate.de
equinate.de
everki.com
deinsportsfreund.de
roto-frank.com
reparaturservice-sommer.de
7i7.de
warm-on.com
consurio.de
sweat-off.ch
tordoc.de
hygiene-shop.com
kuebler-sport
batteriespezialist.de
swmarkt24.de
hipster-baby.de
kik.eu
andries24.de
shop.bauer-blue.de
lotus-design.com
brainbowtoys.de
cleverbuy.de
mondesi.de
powerflex-deutschland.de, www.zimmermann-brems
softwarekaufen24.de
kupplung-vor-ort.com
identible.de
poco.de
akkuteile.de
sportstech.de
foodsetter.de + whiskyfass.de
foodsetter.de + whiskyfass.de
meine-wunschleuchte.de
klingele24.de
verdeck.de
korbmayer.de
verdeck.de
brb-lagertechnik.de
identbase.de
buhl-data.com
motointegrator.de, https:
profichemie.com | geruchskontrolle.de
idealholz-carport.de
1a-yachtcharter.de
smarketer.de
alpeiner.at
patientenfixierung.de
ltt-versand.de
ltt-versand.de
ltt-versand.de
moebelando.de
ltt-versand.de
ltt-versand.de
ltt-versand.de
ltt-versand.de
shop.devita-online.de
sowero.de
masterpro.de
schildershop24.de
kajakprofi.de
lasermed-augenzentren.de
motor-oel-guenstig.de
tbs-aachen.de
scooter-center.com
1plushygiene.de
regold.de
jc-trauringe.de, https:
gideons.de
led-lichtraum.de
uta.com
autoankauf.live
kreidetafel.de
kreidetafel.de
pepperworldhotshop.com
schilderplus.de
solarisgroup.com
turboservice24.de
trooper.ch
greenhero.de
shop.eker-daunen.de
ielectro.ch
dipasch.de
shop.thefineartofbeauty.ch
vnr.de
cossbeauty.de
verdeck.de
rocket-tools.de
ttt48.de
lizenzstar.de
emsraum.de
intra-tec.de
sportstech.de
heavy-tuned.de
market-lifestore.de
equinate.de
hof-university.de
goodsforbusiness.de
grow-guru.com
rameder.de
jonnyfresh.de
motointegrator.de, https:
motointegrator.de, https:
diaprax.de
motorgarten.de
notebookstore.de
schoener-leben-shop.de
mondesi.de
deutsche-kredit-hilfe.de
helme-maedl.de
feine-algen.de
dpf24.de
yoko.de
alurit.de
blum-treuhand.ch
mscrm-addons.com
sunlux24.de
spiel-preis.de
schildershop24.de
shop.pedag.de
schlafshop.ch
gartenfachmarkt24.de
fcb-basketball.de
greensystems-stadtmobiliar.de
photolini.de
ichwillgartenmöbel.de
autoteile-online.at
spiegler.de
toner-dumping.de
tradeo-gmbh.de; www.servershop24.de
motorgarten.de
identbase.de
kunstundmagie.de
vinlorenc.de
institut-fuer-hypnose.de
khs.net
schoene-zaehne-berlin.de
winnersfood.de
yohann.com
Kare.ch
apfelkiste.ch
sauna-life.com
hoffmann-moebel.de
sanitaet24.ch
babyprofi.de
casa-padrino.de
displayhersteller.de
kurzwarenland.de
plotterfolie.de
powerflex-deutschland.de, www.zimmermann-brems
powerflex-deutschland.de, www.zimmermann-brems
phonedepot.ch
schuhglueck.de
Koffer.ch
affengeilebilder24.de
ricks-motorcycles.shop
tavarlin.shop, tavarlin.com
uhrenband.ch
druckexperte.ch
zenfulfillment.com
myaluprofil.de
dd-baustoffe.de
rocket-tools.de
badewanne24.de
creoven.eu
taynie.de
shop.funke-zeitschriften.de
phonelook.ch
klingele24.de
passiondecor.net
oelluxx24.de
whiteboard-direct.de
dewall-design.de
biomazing.ch
naehmaschinen-center.de
radicalracing.de
culpa-inkasso.de
spiel-preis.de
bremer-weinkolleg.de
rameder.de
gutepflege24.de
schneiderprintware.de
identbase.de
getriebe.nrw
ersatzteilshop.de
greenstories.de
einlassband.eu
profichemie.com | geruchskontrolle.de
jc-trauringe.de, https:
auto-bodenheimer.de
cls-med.de
it-welt24.de
kmdi.de
schlummersack.de
amaiva.de
best-software.de
fotichaestli.ch
trendmoebel24.de
powerflex-deutschland.de, www.zimmermann-brems
powerflex-deutschland.de, www.zimmermann-brems
sumico.de
be-glass.de
diekleineschnullerbacke.de
luxnote.de
kreissler24.de
softwaresolution24.com
offerten24.ch
payment-consulting.de
madena.de
ascountry-stone.de
sportauspuff-store.de
renoverta.com
charlesandmarie.de
igienair.de
biodermis-shop.com
stewa.de
schlumberger.de
emmi-dent.de
emmi-dent.de
crossequip.ch
sportshop-triathlon.de
kuheiga.com
bikebox-shop.de
jugard-kuenstner.de
gartenfachmarkt24.de
rameder.de
linus-store.com
myboshi.net
sweat-off.com
dankundtreu.de
in-and-out-consulting.com
ultraschall-welt.de
myoem.de
kraft-reparaturdienst.de
actionsports.de
ahorn-hotels.de
getraenkezapfanlagen.net
sailer-verlag.de
ledlager.de
radicalracing.de
sportnahrung-engel.de
profolio.eu
filterboerse.ch
aqmos.com
lichtwunder.com
rickert-werkzeug.de
jms-fahrzeugteile.de
vorteilstarife.de, https:
fuehlersysteme.de
mondesi.de
gustero.ch
holzboden-direkt.de
lv-kredit.de
klimaworld.com
tradingshenzhen.com
lizenzguru.de
roominabox.de
hno-cvk.charite.de
walkonthewildside.de
galeriebedarf.de
velken.de
coutobelle.de
saunabedarf-schneider.de
ersatzteile.schueco.com
sense-electra.de
inselhotel-potsdam.de
fansport24.de, https:
ichliebedesign.de
pflegedienst-kremer.de
led-leisten.de
schuhe24.de
vorteilstarife.de, https:
bikebox-shop.de
tour-made.de
gabionen24.de
buhl-data.com
neoneverglow.de
Druckspezialist.de
aproposwein.com
belando.ch
oelluxx24.de
fansport24.de, https:
smarketer.de
buehnen.de
hufeland.com
gestalterbank.de
polaris-tours.de
automattenland.de
rocket-tools.de
hemdenbox.de
pch-shop.de
efaflex.de
hans-natur.de
gartenfachmarkt24.de
akkushop.de
terramedus.de
buerotschudi.ch
dein-goldankauf.de
guenstigwohnen.de
jms-fahrzeugteile.de
cinquestore.de
trameo.de
voucherwonderland.com
krauterie.de
auto-id247.com
duschmeister.de
doc-cirrus.com
teegschwendner.de
smatano.de
feine-algen.de
moebel-style.de
tiershop.de
zahnarzt-drseidel.de
i-phonewerkstatt.at
1-stop.shop
kartonagen-schmidt.de
beliani.de
klingspor.de
schuh-okay.shop
bauschnell.de
bierzapfen-shop.com
pin-ag.de
meine-kueche.de
wassermanufaktur.com
gutschild.de
softgarden.de
crazydetectors.com
gusti-leder.de
2-flowerpower.com
senfkorn-kita.de
unternehmen24.info
kickerkult.de
Futterbauer.de
collamin.ch
penfabrik.com
profolio.eu
dittrich-naehmaschinen.de
tauchershop.de
fansport24.de, https:
stanbarry.com
rameder.de
rameder.de
rameder.de
erlich-bauhandel.de
united-promotion.eu
Kindermaxx.de
btec24.de
e-cooline.de
kletterbude.de
mondesi.de
notebookgalerie.de
mondesi.de
sport-tec.de
jonnyfresh.de
livingfloor.com
auto-id247.com
elten.com
sd-dresden.de
patientenfixierung.de
yoga-vidya.de
akkushop.de
akkushop.de
garmin.com
spiel-preis.de
enchantingtravels.de
schutzhuellenprofi.de
btec24.de
it-nerd24.de
teamsport-philipp.de
moebelmarkt.com
wohnpark-binzen.de
profi-tack.de
moebelmarkt.com
winespecials.de
stoffe-hemmers.de
avena.de
knuffelwuff.de
onlinezoo.at
foliencenter24.com
gesundheitsmanufaktur.de
reinica.de
www.teppich-natur.de
revox.com
dailydream.de
motointegrator.de, https:
klippshop.at
aurasoma.de
heimundgarten24.de
herder.de
badfabrik24.de
fuetternundfit.de
shkshop.com
zeg-holz.de
exxatron.com
nilo-hamburg.de
schweizer-edelmetallhandel.ch
markusdoettling.de
spiegler.de
kochblume.de
armypoint.ch
schockemoehle-sports.com
dieklimafreunde.de
hoppediz.de
camelot-shop.de
noelie.com
parkcontrol24.de
tuning-parts24.de
schmusewolke.de
myspa.me
panda-tierversicherung.com
moebelmarkt.com
auto-id247.com
kroschke.de
kraus-shop.at
sanetta.de
zapf-garagen.de
moebelmarkt.com
zapf-garagen.de
seezon.co
deitert.de
icelandic-horse.com
marcelpaa.com
venturama-solar.de
wunderbad.eu
alltickets.ch
outdoorsurvival.ch
Bps-protect.de
einfach-dichtungen.de
truebenecker.de
deitert.de
deitert.de
schockemoehle.net
jansen-france.fr
1001coques.fr
schlafshop.ch
andre.reuss@omg.de
rollenplus.de
buchmann.ch
shop.shp-primaflex.com
blackchilimesser.de
biggastro.com
surfari.ch
bresko.de
lanakk.com
locus-bauunternehmen.de
kik.eu
warimex.de
mySWOOOP.de
janado.de
shop.trikon.it
birkenstock.com
atvshop.ch
tropic-shop.de
vidal-teppiche.com
faber-castell.fr
bundesverkehrsamt.online
lowa.com
lederfischer.com
jubee.de
qconnex.com
koffer24.de
bigmoebel.com
leuchtreklameparadies.de
Floristikcenter.ch
kern-bildungsgesellschaft.de
akkuman.de
planen-online-shop.de
mks-autoteile.de
tictex.com
tebo.ag
shop.meta-online.com
decocity-muenchen.de
hobbybaecker.de
ausdruckslos.de
nutrilovers.de
equinate.de
freyerleben.de
c-g-w.net
spinnrad.de
spinnrad.ch
roto-dachfenster.de
roto-frank.com
motointegrator.de, https:
motointegrator.de, https:
moebel-style.de
Kindermaxx.de
elten.com
ltt-versand.de
gusti-leder.de
motointegrator.de, https:
passiondecor.net
smarketer.de
gusti-leder.de
gusti-leder.de
udobaer.de
Kindermaxx.de
gusti-leder.de
smarketer.de
passiondecor.net
passiondecor.net
gusti-leder.de
klippshop.at
finvia.fo
motointegrator.de, https:
motointegrator.de, https:
mybag factory
treppini.de
derfarbeimer.de
tarif.planbnetzero-energy.com
media-dental.de
wirkaufenihrenabfall.de
hofa.de
shop.purway.de, https:
shop.purway.de, https:
bettwaesche-mit-stil.de
sofein.ch
fain.de
swisstrin.ch
pcdt.fr
spuelenhandel.de
campingbedarf24.ch
meine-steigplatten.de
dubimoebel.de
velvet-trading.de
proofficedüsseldorf.de
treppenshop-dresden.de
stex24.com
sportnahrung-kwax.de
hdwm.de
hd-elektrotechnik.com
panto-agrar.de
hometiger.de
etc-shop.de
sausewind-shop.com
freyerleben.de
shop.bauer-blue.de
passiondecor.net
gusti-leder.de
protect-home.de
stex24.com
schlummersack.de
pch-shop.de
onlinezoo.at
mouldshop.de
faber-castell.fr
sierbach.de
khroom-sport.com
helmtrophy.com
original-seiffener-volkskunst.de
potec-gmbh.de
monchoujewelry.de
santosgrills.de
rothsteiner.de
handball.de
erassential.com
buddystar.de
locus-bauunternehmen.de
chefstore.ch
mentortools.com
easy-lakeballs.de
daebak.de
easy-lakeballs.de
bresser.de
bresser.de
bresser.de
ongnamo-versand.de
launch-europe-shop.de
mail.com
meowbaby.de
mbs-medizintechnik.com
holzhandel-vogt.de
systeme-software24.com
flatexdegiro.com
belanoti.com
madgon.com
ichmachkita.de
jorinde-shop.ch
glueck-in-sicht.de | intermar-apartments.de
brekstar.de
faber-castell.fr
sbj-sportland.de
vasenglueck.de
www.panto-heimtier.de
planville.de
tymbia.com
aqua-comfort.net
shop.bauer-blue.de
bresser.de
bresser.de
identmarket.de
radixweb.ch
wpc-shop24.de
easy-lakeballs.de
murmelkiste.com
beautylinestore.de
bau-sauber.com
gcfootwear.com
blo-lackiersysteme.de
daszelt.ch
smatano.de
axisbits.ch
esmee.ch
iamcreative.ch
haushaltsparadies.at
erwachsenenhotel-erzgebirge.de
bresser.de
paradies-bettenshop.de
adenauer.com
tonitrus.de
drachenfels-design.de | https:
hesselink24.de
diejawue.de
der-grashuepfer-shop.de
kunstpflanze.de
hoelscher-wasserbau.de
tmn-shop.de
kluth24.com
pcdt.fr
deitert.de
bonebrox.com
tmn-shop.de
wpc-shop24.de
nuprevento.shop
sofortwechsel24.de
igold-Deutschland.de
krohne.com
monchoujewelry.de
azowo.com
bomi-handel.de
hds-tore.de
eat-skincare.com
jorinde-shop.ch
absperren24.de
Lizenzdiscount.de
bsv-hausmeisterservice.de
druckexperte.ch
ltt-versand.de
druckexperte.ch
vnr.de
gusti-leder.de
gusti-leder.de
greenhero.de
marcelpaa.com
passiondecor.net
tonis-pizzaoefen.shop
sucietto.de
schutzhuellenprofi.de
passiondecor.net
onlinezoo.at
ltt-versand.de
gusti-leder.de
motointegrator.de, https:
onlinezoo.at
gusti-leder.de
gusti-leder.de
Kindermaxx.de
feelgood-shop.com
moebel-style.de
fansport24.de, https:
klippshop.at
faber-castell.fr

        """

        # Rozbijamy tekst na linie, czyścimy ze spacji i odrzucamy puste wiersze
        urls_to_scan = [line.strip() for line in raw_text.split('\n') if line.strip()]
        
        results = []
        total = len(urls_to_scan)
        
        print(f"DEBUG: Przygotowano {total} domen do skanowania.")

        for i, domain in enumerate(urls_to_scan):
            # Dodaj https:// jeśli brakuje
            url = domain if domain.startswith('http') else f"https://{domain}"
            
            print(f"Postęp: {i+1}/{total} - Skanuję: {url}")
            
            try:
                res = await PrivacyAuditService.run_audit(url)
                results.append(res)
            except Exception as e:
                print(f"!!! BŁĄD na {url}: {str(e)}")
                results.append({
                    "url": url, 
                    "is_shopify": False, 
                    "error": "Błąd połączenia"
                })
                
        return results