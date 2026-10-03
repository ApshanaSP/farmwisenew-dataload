/**
 * Vocabulary for synthetic grievances: complaint text in English, Tamil and
 * Tanglish, landmarks, names, typed street names and officer remarks.
 *
 * Everything here is invented. Names are common Tamil given names, not real
 * people; mobile numbers and email domains are chosen to be obviously fake
 * (see generate-synthetic-grievances.js).
 *
 * Templates take slots:
 *   {sub}   sub-type label           {subl}  sub-type label, lower case
 *   {place} "near <landmark>" / "in our street" / "on <street>" (English only)
 *   {dur}   a duration in the template's language
 */

// ---------------------------------------------------------------- slots --

const DUR = {
  en: ["for the past {n} days", "for over a week", "for two weeks now", "for the last {n} days", "since last Monday", "for almost a month"],
  ta: ["{n} நாட்களாக", "ஒரு வாரமாக", "இரண்டு வாரங்களாக", "கடந்த {n} நாட்களாக", "ஒரு மாதமாக"],
  tanglish: ["{n} naala", "one week ah", "rendu vaaram ah", "last {n} days ah", "oru maasama"]
};

const IMPACT = {
  en: [
    "Children going to school are badly affected.",
    "Elderly people cannot walk on this stretch.",
    "There is a hospital nearby and ambulances find it hard to pass.",
    "The smell is unbearable.",
    "There is a risk of dengue in the area.",
    "Accidents are happening frequently.",
    "Women are afraid to walk here at night.",
    "Shops and residents are suffering.",
    "Senior citizens in our street are struggling."
  ],
  ta: [
    "பள்ளி குழந்தைகள் மிகவும் சிரமப்படுகிறார்கள்.",
    "முதியவர்கள் நடக்க முடியவில்லை.",
    "அருகில் மருத்துவமனை உள்ளது.",
    "துர்நாற்றம் வீசுகிறது.",
    "டெங்கு பரவும் அபாயம் உள்ளது.",
    "விபத்து ஏற்படும் அபாயம் உள்ளது.",
    "இரவில் பெண்கள் நடந்து செல்ல பயப்படுகிறார்கள்."
  ],
  tanglish: [
    "School pasanga romba kashtapadranga.",
    "Periyavanga nadakka mudiyala.",
    "Pakkathula hospital irukku, ambulance vara mudiyala.",
    "Romba smell varudhu.",
    "Dengue vara chance irukku.",
    "Accident aagura maari irukku."
  ]
};

const REPEAT = {
  en: [
    "I have already complained twice but no action taken.",
    "This is a repeated complaint; still not attended.",
    "Complained 3 times through the helpline, no action so far.",
    "Already complained last month, still not cleared."
  ],
  ta: ["ஏற்கனவே இரண்டு முறை புகார் அளித்தும் நடவடிக்கை இல்லை.", "பலமுறை புகார் செய்தும் பயனில்லை."],
  tanglish: ["Already rendu thadava complaint pannom, no action.", "Evlo thadava sonnalum yaarum varala."]
};

const PLEA = {
  en: ["Kindly take immediate action.", "Please resolve at the earliest.", "Request you to look into this urgently.", "Please do the needful.", "Kindly send someone to inspect."],
  ta: ["உடனடியாக நடவடிக்கை எடுக்கவும்.", "தயவுசெய்து விரைவில் சரி செய்யவும்.", "அதிகாரிகள் நேரில் வந்து பார்க்கவும்."],
  tanglish: ["Please seekiram action edunga.", "Konjam urgent ah paarunga sir.", "Udane vandhu paarunga please."]
};

const LANDMARKS = {
  en: [
    "Opp. Govt Higher Secondary School", "Near Murugan Temple bus stop", "Behind Ration shop",
    "Near Amma Unavagam", "Opp. Primary Health Centre", "Next to Corporation Park",
    "Near Anganwadi centre", "Near Pillaiyar Kovil", "Near SBI ATM", "Behind Bus Depot",
    "Near Railway station entrance", "Opp. St. Mary's Church", "Near Mosque", "Near Aavin milk booth",
    "Near Government Hospital", "Near Corporation School", "Opp. Petrol bunk", "Near Mariamman Kovil",
    "Near Post Office", "Behind Vegetable market", "Near Water tank", "Opp. Kalyana Mandapam",
    "Near EB office", "Near Police booth", "Near Children's park gate"
  ],
  ta: [
    "அரசு மேல்நிலைப் பள்ளி எதிரில்", "முருகன் கோயில் பேருந்து நிறுத்தம் அருகில்", "ரேஷன் கடை பின்புறம்",
    "அம்மா உணவகம் அருகில்", "ஆரம்ப சுகாதார நிலையம் எதிரில்", "பிள்ளையார் கோயில் அருகில்",
    "மாநகராட்சி பள்ளி அருகில்", "காய்கறி சந்தை பின்புறம்", "அங்கன்வாடி மையம் அருகில்"
  ]
};

// ------------------------------------------------ specific observations --
// What exactly the resident sees, so two complaints about the same kind of problem
// still read like two different people writing. Slots: {street} {locality} {n} {k} (a handful) {m} (a few
// feet) {len} (metres) {house} {hour} {days} {day}; filled in generate-synthetic-grievances.js (writeText).

const OPENER = {
  en: [
    "I live at door no. {house}, {street}.", "This is about {street} in {locality}.", "Residents of {locality} want to report this.",
    "Writing on behalf of our residents' welfare association ({n} families).", "I use this stretch of {street} every day.",
    "I run a small shop on {street}.", "My elderly parents live on {street}.", "We are tenants in the flats on {street}.",
    "Complaint from the residents of {street}.", "I am a resident of {locality} for many years."
  ],
  ta: [
    "நான் {street}, கதவு எண் {house}-ல் வசிக்கிறேன்.", "{locality} பகுதி மக்கள் சார்பாக இந்த புகார்.", "எங்கள் குடியிருப்போர் நலச் சங்கம் ({n} குடும்பங்கள்) சார்பாக எழுதுகிறேன்.",
    "{street}-ல் கடை வைத்திருக்கிறேன்.", "தினமும் {street} வழியாகச் செல்கிறேன்."
  ],
  tanglish: [
    "Naan {street} la door no {house} la irukken.", "{locality} people sarbaaga indha complaint.", "{street} la kadai vechurukken.",
    "Daily {street} vazhiya dhaan poren."
  ]
};

const DETAIL = {
  Garbage: {
    en: [
      "The heap is now about {m} feet long and blocks half the road.", "Mostly vegetable waste from the market and plastic covers.",
      "Construction debris has also been dumped along with household waste.", "Coconut shells and tender coconut waste are piling up near the shop.",
      "Crows and stray dogs pull the waste across the road every morning.", "The bin has no lid and the waste gets wet in the rain.",
      "The collection vehicle comes only once in {days} days now.", "Hotel waste is dumped here late at night."
    ],
    ta: ["சந்தையின் காய்கறிக் கழிவும் பிளாஸ்டிக் பைகளும் குவிந்துள்ளன.", "கட்டிடக் கழிவும் சேர்த்துக் கொட்டப்படுகிறது.", "காகங்களும் நாய்களும் குப்பையை சாலை முழுவதும் இழுக்கின்றன.", "குப்பை வண்டி {days} நாளுக்கு ஒரு முறைதான் வருகிறது."],
    tanglish: ["Market kaaikari waste um plastic cover um kuvinjirukku.", "Night la hotel waste konduvandhu kottranga.", "Kuppai vandi {days} naalukku oru thadava dhaan varudhu."]
  },
  "Road and Footpath": {
    en: [
      "The pit is about {m} feet wide and more than a foot deep.", "Metal plates left after the cable work are sticking out.",
      "The patch work done last month has already come off.", "Loose gravel on the surface makes bikes skid.",
      "The footpath slabs are broken and some are missing.", "Water collects in the pit and hides it from motorists.",
      "An auto overturned here on {day} evening.", "The road was dug for a pipeline and never relaid."
    ],
    ta: ["பள்ளம் சுமார் {m} அடி அகலம் உள்ளது.", "கடந்த மாதம் போட்ட ஒட்டுவேலை ஏற்கனவே பெயர்ந்துவிட்டது.", "நடைபாதைக் கற்கள் உடைந்து காணாமல் போயுள்ளன.", "குழாய் பதிக்கத் தோண்டிய சாலை மீண்டும் போடப்படவில்லை."],
    tanglish: ["Pallam {m} adi agalam irukku.", "Pona maasam potta patch work already poiduchu.", "Pipeline ku thondunadhu appadiye irukku, road podala."]
  },
  "Street Light": {
    en: [
      "{k} lights in a row near the junction are off.", "The light flickers and goes off after 9 pm.", "The pole is leaning and the wire is hanging low.",
      "The light was replaced last month but stopped working again.", "The lamp stays on during the day and is off at night.",
      "Chain snatching happened here last week because it is dark.", "The junction box at the bottom of the pole is open."
    ],
    ta: ["சந்திப்பு அருகே வரிசையாக {k} விளக்குகள் எரியவில்லை.", "கம்பம் சாய்ந்து கம்பி தாழ்வாகத் தொங்குகிறது.", "கடந்த வாரம் இருட்டில் செயின் பறிப்பு நடந்தது."],
    tanglish: ["Junction pakkathula {k} light eriyala.", "Pole saanju wire keezha thongudhu.", "Pona vaaram dark la chain snatching nadandhuchu."]
  },
  "Public Health": {
    en: [
      "Several children in our lane have had fever this week.", "Mosquito larvae are visible in the stagnant water near the houses.",
      "The fogging vehicle has not come to our street this month.", "A dead dog has been lying near the corner since {day}.",
      "Dogs bit {k} people in the last month.", "The drain behind the houses is breeding mosquitoes."
    ],
    ta: ["இந்த வாரம் எங்கள் தெருவில் பல குழந்தைகளுக்குக் காய்ச்சல்.", "தேங்கிய நீரில் கொசுப் புழுக்கள் தெரிகின்றன.", "இந்த மாதம் கொசு மருந்து அடிக்க யாரும் வரவில்லை."],
    tanglish: ["Indha vaaram street la neraya pasangalukku fever.", "Thengi nikkura thanni la kosu puzhu theriyudhu.", "Indha maasam fogging vandi varala."]
  },
  "Water Stagnation": {
    en: [
      "Water stands knee-deep near the bus stop after every rain.", "The drain inlet is blocked with plastic and silt.",
      "Sewage is mixing with the rain water and entering houses.", "The water does not drain even two days after the rain.",
      "The road level was raised and now water enters the houses.", "The stagnant water has turned green and smells."
    ],
    ta: ["ஒவ்வொரு மழைக்கும் பேருந்து நிறுத்தம் அருகே முழங்கால் அளவு தண்ணீர் தேங்குகிறது.", "வடிகால் வாய் பிளாஸ்டிக் மற்றும் சேற்றால் அடைபட்டுள்ளது.", "கழிவுநீர் மழைநீருடன் கலந்து வீடுகளுக்குள் வருகிறது."],
    tanglish: ["Ovvoru mazhaikkum bus stop kitta muttu alavu thanni nikkudhu.", "Drain vaai plastic la block aayirukku.", "Sewage mazhai thanni kooda veetukulla varudhu."]
  },
  "Storm Water Drains": {
    en: [
      "The trench is open for {len} metres with no barricade or warning light.", "The contractor left the site {days} days ago.",
      "Excavated soil is dumped on the road.", "There is no way for pedestrians to cross to the other side.",
      "The slab over the drain is broken at the corner."
    ],
    ta: ["{len} மீட்டர் பள்ளம் தடுப்பு இல்லாமல் திறந்து கிடக்கிறது.", "ஒப்பந்ததாரர் {days} நாட்களுக்கு முன் பணியை நிறுத்திவிட்டார்.", "தோண்டிய மண் சாலையில் கொட்டப்பட்டுள்ளது."],
    tanglish: ["{len} meter pallam barricade illama thirandhu kidakku.", "Contractor {days} naala varala.", "Thondina mann road la kottirukkanga."]
  },
  Flood: {
    en: ["Water has entered the ground floor houses.", "Elderly people in our street need to be shifted.", "Power is cut and the water keeps rising.", "The canal nearby is overflowing into the street."],
    ta: ["தரைதள வீடுகளுக்குள் தண்ணீர் புகுந்துவிட்டது.", "முதியவர்களை வெளியேற்ற வேண்டும்.", "அருகிலுள்ள கால்வாய் நிரம்பி தெருவுக்குள் வழிகிறது."],
    tanglish: ["Ground floor veetukulla thanni vandhuduchu.", "Periyavangala shift pannanum.", "Pakkathu canal overflow aagi street kulla varudhu."]
  },
  "Park and Playground": {
    en: ["The swings are broken and the children could get hurt.", "The park gate is locked during morning walking hours.", "The walking track is covered with dry leaves and garbage.", "The lights in the park do not work in the evening."],
    ta: ["ஊஞ்சல்கள் உடைந்துள்ளன.", "காலை நடைப்பயிற்சி நேரத்தில் பூங்கா கதவு பூட்டியுள்ளது."],
    tanglish: ["Swing odanjirukku, pasangalukku adipadum.", "Morning walking time la park gate lock pannirukkanga."]
  },
  "Public Toilet": {
    en: ["There is no water supply in the toilet.", "The toilet is locked most of the day.", "The septic tank is overflowing onto the road.", "There is no light inside at night."],
    ta: ["கழிப்பறையில் தண்ணீர் வசதி இல்லை.", "பெரும்பாலான நேரம் கழிப்பறை பூட்டியுள்ளது."],
    tanglish: ["Toilet la thanni illa.", "Neraya neram toilet lock pannirukkanga."]
  },
  MEGA: {
    en: ["The half-finished work has narrowed the road to one lane.", "Cables are lying across the new footpath.", "The new footpath is already being used for parking."],
    ta: ["பாதியில் நிற்கும் பணியால் சாலை ஒற்றை வழியாகிவிட்டது."],
    tanglish: ["Paadhi velai la road one lane aayiduchu."]
  },
  "Air Quality": {
    en: ["The smoke is worst between 6 and 8 in the evening.", "Children in the school nearby are coughing.", "Dust from the construction site covers the houses."],
    ta: ["மாலை 6 முதல் 8 மணி வரை புகை அதிகம்.", "கட்டுமானத் தூசி வீடுகளை மூடுகிறது."],
    tanglish: ["Evening 6 to 8 pugai romba jaasthi.", "Construction dust veedu fulla padiyudhu."]
  }
};

const WHEN = {
  en: ["It is worst in the morning around {hour} am.", "The problem is worse at night.", "It started on {day} and has only got worse.", "It gets worse every time it rains.", "It is like this every weekend."],
  ta: ["காலை {hour} மணியளவில் மிக மோசமாக உள்ளது.", "இரவில் நிலைமை இன்னும் மோசம்.", "மழை பெய்யும் ஒவ்வொரு முறையும் மோசமாகிறது."],
  tanglish: ["Morning {hour} mani ku romba mosam.", "Night la innum mosam.", "Mazhai peidha ovvoru thadavaiyum mosam aagudhu."]
};

const EXTENT = {
  en: ["About {n} houses on the street are affected.", "The whole stretch of around {len} metres is affected.", "{n} families in our lane face this daily.", "Two schools and a temple are on this stretch."],
  ta: ["தெருவில் சுமார் {n} வீடுகள் பாதிக்கப்பட்டுள்ளன.", "எங்கள் சந்தில் {n} குடும்பங்கள் தினமும் சிரமப்படுகின்றன."],
  tanglish: ["Street la {n} veedu affect aagudhu.", "Engal sandhula {n} family daily kashtapadranga."]
};

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

// -------------------------------------------------------- core sentences --
// Keyed by sub-type label first, then by category. Each entry: { en, ta, tanglish }.

const BY_SUBTYPE = {
  "Removal of Garbage": {
    en: ["Garbage is piled up {place} and has not been removed {dur}.", "Huge garbage dump {place}, not cleared {dur}. Stray dogs are spreading it on the road.", "Waste has been lying {place} {dur} and nobody from conservancy has come."],
    ta: ["எங்கள் தெருவில் குப்பை {dur} அகற்றப்படவில்லை. துர்நாற்றம் தாங்க முடியவில்லை.", "சாலை ஓரத்தில் குப்பை மலை போல் குவிந்துள்ளது. {dur} யாரும் அகற்றவில்லை."],
    tanglish: ["Engal street la garbage {dur} eduklala, romba smell varudhu.", "Road orathula kuppai malai maari irukku, yaarum edukala."]
  },
  "Overflowing of Garbage Bin": {
    en: ["The garbage bin {place} is overflowing {dur}; waste is spilling onto the road."],
    ta: ["குப்பைத் தொட்டி நிரம்பி வழிகிறது. குப்பை சாலையில் சிதறிக் கிடக்கிறது."],
    tanglish: ["Bin full ah overflow aagudhu, road fulla kuppai irukku."]
  },
  "Burning of Garbage": {
    en: ["People are burning garbage {place} every evening. The smoke is unbearable."],
    ta: ["தினமும் மாலையில் குப்பை எரிக்கப்படுகிறது. புகையால் மூச்சு விட முடியவில்லை."],
    tanglish: ["Daily evening kuppai erikiranga, pugai romba jaasthi, moochu vida mudiyala."]
  },
  "Burning of Garbage at Dumping Ground": {
    en: ["Garbage at the dumping ground is burning {dur}. Thick smoke is covering the whole area."],
    ta: ["குப்பைக் கிடங்கில் {dur} தீ எரிகிறது. புகை முழு பகுதியையும் சூழ்ந்துள்ளது."],
    tanglish: ["Dumping ground la {dur} fire eriyudhu, area fulla pugai."]
  },
  "Absenteeism of Door to door garbage Collector": {
    en: ["Door to door garbage collector has not come to our street {dur}."],
    ta: ["வீடு வீடாக குப்பை சேகரிப்பவர் {dur} வரவில்லை."],
    tanglish: ["Door to door kuppai collect panra aal {dur} varala."]
  },
  "Pot hole fill up / Repairs to the damaged surface": {
    en: ["There is a big pothole {place}. Two-wheelers are falling at night.", "Road surface is badly damaged {place} with deep potholes {dur}.", "Deep pothole {place} is filled with rain water and not visible to motorists."],
    ta: ["சாலையில் பெரிய பள்ளம் உள்ளது. இருசக்கர வாகன ஓட்டிகள் இரவில் விழுகிறார்கள்.", "சாலை மிகவும் சேதமடைந்துள்ளது. {dur} சரி செய்யப்படவில்லை."],
    tanglish: ["Road la periya pallam irukku, bike la pora mudiyala.", "Road romba damage aayiduchu, night la accident aagudhu."]
  },
  "Non burning of Street lights": {
    en: ["Street light {place} is not working {dur}. The street is completely dark at night.", "Three street lights in a row are not burning {place}."],
    ta: ["தெருவிளக்கு {dur} எரியவில்லை. இரவில் பெண்கள் நடந்து செல்ல பயப்படுகிறார்கள்."],
    tanglish: ["Street light {dur} eriyala, night la nadandhu poga bayama irukku."]
  },
  "Electric shock due to street light": {
    en: ["People are getting electric shock from the street light pole {place}. Live wire is exposed."],
    ta: ["தெருவிளக்கு கம்பத்தில் மின்சாரம் தாக்குகிறது. மின் கம்பி வெளியே தெரிகிறது."],
    tanglish: ["Street light pole la current shock adikkudhu, live wire veliya theriyudhu."]
  },
  "Street Dogs": {
    en: ["Street dogs menace {place} is very high. A pack of dogs chases two-wheelers and children.", "Stray dogs bit a person {place} last week; please catch them."],
    ta: ["தெருநாய்கள் தொல்லை அதிகமாக உள்ளது. குழந்தைகள் பள்ளிக்குச் செல்ல பயப்படுகிறார்கள்."],
    tanglish: ["Street dogs romba jaasthi, pasanga school ku poga bayapadranga."]
  },
  "Mosquito Menace": {
    en: ["Mosquito menace is very high {place}. Please do fogging and spray larvicide."],
    ta: ["கொசுத் தொல்லை அதிகமாக உள்ளது. டெங்கு பரவும் அபாயம் உள்ளது. கொசு மருந்து அடிக்கவும்."],
    tanglish: ["Kosu thollai romba jaasthi, dengue vara chance irukku. Fogging pannunga."]
  },
  "Public Health / Dengue / Malaria / Gastro Enteritis": {
    en: ["Two people in our street have dengue fever. Please take preventive measures {place}."],
    ta: ["எங்கள் தெருவில் இருவருக்கு டெங்கு காய்ச்சல். தடுப்பு நடவடிக்கை எடுக்கவும்."],
    tanglish: ["Engal street la rendu perukku dengue fever, konjam fogging pannunga."]
  },
  "Stagnation of Water": {
    en: ["Rain water is stagnating {place} {dur} and mosquitoes are breeding.", "Knee-deep water stagnation {place}; vehicles cannot pass."],
    ta: ["மழைநீர் {dur} தேங்கி நிற்கிறது. கொசுக்கள் அதிகரித்துள்ளன.", "முழங்கால் அளவு தண்ணீர் தேங்கியுள்ளது. வாகனங்கள் செல்ல முடியவில்லை."],
    tanglish: ["Mazhai thanni {dur} thengi nikkudhu, kosu romba jaasthi aayiduchu.", "Muttu varaikkum thanni nikkudhu, vandi poga mudiyala."]
  },
  "Covering Manholes of Storm Water Drain": {
    en: ["Storm water drain manhole {place} is left open without a cover. Very dangerous at night."],
    ta: ["மழைநீர் வடிகால் மூடி திறந்த நிலையில் உள்ளது. இரவில் மிகவும் ஆபத்தாக உள்ளது."],
    tanglish: ["Drain manhole moodi illama open ah irukku, night la romba danger."]
  },
  "Removal of Fallen Trees": {
    en: ["A big tree has fallen across the road {place} after the rain. Traffic is blocked.", "Fallen tree {place} is resting on the compound wall and electric wires."],
    ta: ["மழையால் பெரிய மரம் சாலையில் விழுந்து கிடக்கிறது. போக்குவரத்து பாதிக்கப்பட்டுள்ளது."],
    tanglish: ["Periya maram road la vizhundhuduchu, traffic block aagudhu."]
  },
  "Water entering Home/Shop": {
    en: ["Rain water is entering houses {place}. We need immediate help to pump it out."],
    ta: ["வீட்டுக்குள் மழைநீர் புகுந்துவிட்டது. உடனடியாக உதவி தேவை."],
    tanglish: ["Veetukulla mazhai thanni vandhuduchu, udane help pannunga."]
  },
  "Trap at home/ any place": {
    en: ["An elderly couple is trapped at home {place} due to flooding. Please send rescue."],
    ta: ["வெள்ளத்தால் வீட்டில் சிக்கியுள்ளோம். மீட்புக் குழுவை அனுப்பவும்."],
    tanglish: ["Flood nala veetla maatikittom, rescue team anuppunga."]
  },
  "EB wire cut/Spark seen": {
    en: ["EB wire has fallen into the flood water {place}; sparks were seen."],
    ta: ["மின் கம்பி வெள்ள நீரில் விழுந்துள்ளது. தீப்பொறி தெரிகிறது."],
    tanglish: ["EB wire thanni la vizhundhu spark varudhu, romba danger."]
  },
  "Repairs to existing Footpath": {
    en: ["The footpath {place} is broken and pedestrians are forced to walk on the road."],
    ta: ["நடைபாதை உடைந்து கிடக்கிறது. மக்கள் சாலையில் நடக்க வேண்டியுள்ளது."],
    tanglish: ["Footpath odanju irukku, road la dhaan nadakka vendi irukku."]
  },
  "Illegal Parking on foot path": {
    en: ["Vehicles are left on the footpath {place} every day; pedestrians have to walk on the road."],
    ta: ["நடைபாதையில் வாகனங்கள் நிறுத்தப்படுகின்றன. மக்கள் சாலையில் நடக்க வேண்டியுள்ளது."],
    tanglish: ["Footpath mela vandi ellam nikkudhu, road la dhaan nadakka vendi irukku."]
  },
  "Desilting of Drain": {
    en: ["The drain {place} is full of silt and water is not flowing."],
    ta: ["வடிகால் முழுவதும் வண்டல் நிரம்பியுள்ளது. தண்ணீர் வெளியேறவில்லை."],
    tanglish: ["Drain fulla mannu, thanni pogala."]
  },
  "Obstruction of Water Flow": {
    en: ["Water flow in the drain {place} is blocked by debris and plastic."],
    ta: ["வடிகால் அடைப்பு ஏற்பட்டுள்ளது. தண்ணீர் வெளியேறவில்லை."],
    tanglish: ["Drain block aayiduchu, thanni pogala."]
  },
  "New Drain Construction": {
    en: ["There is no storm water drain {place}. Every rain the whole street goes under water."],
    ta: ["எங்கள் தெருவில் மழைநீர் வடிகால் இல்லை. மழை பெய்தால் தெரு முழுவதும் தண்ணீர்."],
    tanglish: ["Engal street la drain-e illa, mazhai vandha street fulla thanni."]
  },
  "Burning of street light in daytime": {
    en: ["Street lights {place} are burning even during the day, wasting power."],
    ta: ["பகலிலும் தெருவிளக்குகள் எரிகின்றன. மின்சாரம் வீணாகிறது."],
    tanglish: ["Pagal-la kooda street light eriyudhu, current waste aagudhu."]
  },
  "Online Payment Issue": {
    en: ["Online payment of tax failed but the amount was debited from my bank account."],
    ta: ["ஆன்லைனில் வரி செலுத்தியபோது பணம் கழிக்கப்பட்டது, ஆனால் ரசீது வரவில்லை."],
    tanglish: ["Online la tax katta try panninen, amount cut aayiduchu aana receipt varala."]
  },
  "Name Error (Spelling Related)": {
    en: ["Need correction of name spelling in my voter ID."],
    ta: ["வாக்காளர் அடையாள அட்டையில் பெயர் பிழை உள்ளது. திருத்தவும்."],
    tanglish: ["Voter ID la peru thappa irukku, correct pannunga."]
  },
  "Change of Address in Electoral Roll": {
    en: ["Change of address in the voter list not updated even after applying."],
    ta: ["வாக்காளர் பட்டியலில் முகவரி மாற்றம் இன்னும் செய்யப்படவில்லை."],
    tanglish: ["Voter list la address maathala, apply panniyum update aagala."]
  },
  "Unauthorized / Illegal Construction": {
    en: ["Unauthorised construction of additional floors going on {place} without plan approval."],
    ta: ["அனுமதியின்றி கூடுதல் மாடி கட்டப்படுகிறது. நடவடிக்கை எடுக்கவும்."],
    tanglish: ["Permission illama extra floor kattikittu irukanga."]
  },
  "Violation of DCR/Building By laws": {
    en: ["The building {place} has no setback, violating DCR norms."],
    ta: ["கட்டட விதிகளை மீறி இடைவெளி விடாமல் கட்டடம் கட்டப்பட்டுள்ளது."],
    tanglish: ["Rules-a meeri gap vidaama building kattirukanga."]
  },
  "Building Plan Sanction": {
    en: ["My building plan approval application has been pending {dur}."],
    ta: ["கட்டட வரைபட அனுமதி விண்ணப்பம் {dur} நிலுவையில் உள்ளது."],
    tanglish: ["Building plan approval {dur} pending la irukku."]
  },
  "Complaints related to Property Tax": {
    en: ["Property tax paid online but receipt not generated.", "Property tax demand is wrongly calculated for my house."],
    ta: ["சொத்து வரி செலுத்தியும் ரசீது வரவில்லை."],
    tanglish: ["Property tax kattiyum receipt varala, konjam check pannunga."]
  },
  "Name not found in the Electoral Roll": {
    en: ["My name is not found in the electoral roll though I voted in the last election."],
    ta: ["வாக்காளர் பட்டியலில் என் பெயர் இல்லை. சேர்க்கவும்."],
    tanglish: ["Voter list la en peru illa, add pannunga please."]
  }
};

const BY_CATEGORY = {
  Garbage: {
    en: ["Issue regarding {subl} {place}. Please send the conservancy team.", "{sub}: the problem {place} has continued {dur}."],
    ta: ["குப்பை பிரச்சினை: {sub}. {dur} தீர்வு இல்லை.", "எங்கள் பகுதியில் {sub} பிரச்சினை உள்ளது."],
    tanglish: ["{sub} problem {dur} irukku, yaarum kandukala."]
  },
  "Road and Footpath": {
    en: ["Complaint about {subl} {place}.", "{sub}: this stretch {place} needs attention {dur}."],
    ta: ["{sub} தொடர்பாக புகார். இந்த சாலைப் பகுதியை உடனே கவனிக்கவும்."],
    tanglish: ["{sub} problem irukku, konjam indha road-a paarunga."]
  },
  "Street Light": {
    en: ["{sub} {place}. Please send the electrical team.", "Street light problem {place}: {subl}."],
    ta: ["தெருவிளக்கு பிரச்சினை: {sub}. மின் பிரிவு உடனே கவனிக்கவும்."],
    tanglish: ["Street light problem: {sub}. Konjam seekiram paarunga."]
  },
  "Public Health": {
    en: ["Complaint regarding {subl} {place}.", "{sub}: residents {place} are facing a health hazard {dur}."],
    ta: ["{sub} தொடர்பான பிரச்சினை எங்கள் பகுதியில் உள்ளது. சுகாதாரத் துறை உடனே கவனிக்கவும்."],
    tanglish: ["{sub} problem engal area la irukku, health department konjam paarunga."]
  },
  "Water Stagnation": {
    en: ["Complaint regarding {subl} {place}. Water does not drain even a day after rain."],
    ta: ["{sub} தொடர்பாக புகார். மழை பெய்தால் தெரு முழுவதும் தண்ணீர் தேங்குகிறது."],
    tanglish: ["{sub} problem, mazhai vandha street fulla thanni nikkudhu."]
  },
  "Storm Water Drains": {
    en: ["The storm water drain work {place} has no barricading; a two-wheeler fell into the pit."],
    ta: ["வடிகால் பணி நடக்கும் இடத்தில் தடுப்பு வேலி இல்லை. இருசக்கர வாகனம் பள்ளத்தில் விழுந்தது."],
    tanglish: ["Drain work nadakkura idathula barricade illa, oru bike pallathula vizhundhuduchu."]
  },
  Flood: {
    en: ["Our street {place} is flooded. {sub}: please send help.", "Families {place} are stranded by flood water. {sub}."],
    ta: ["எங்கள் தெரு வெள்ளத்தில் மூழ்கியுள்ளது. {sub}: உதவி தேவை."],
    tanglish: ["Engal street fulla flood, {sub} - udane help pannunga."]
  },
  "Park and Playground": {
    en: ["{sub} in the park near our street. Please look into it.", "The park {place} is poorly maintained: {subl}."],
    ta: ["பூங்கா சரியாக பராமரிக்கப்படவில்லை. {sub} பிரச்சினை உள்ளது."],
    tanglish: ["Park sariya maintain pannala, {sub} problem irukku."]
  },
  "Public Toilet": {
    en: ["The public toilet {place} is not maintained. {sub}.", "Complaint regarding {subl} {place}; the problem has continued {dur}."],
    ta: ["பொதுக் கழிப்பறை தொடர்பான புகார்: {sub}. உடனே கவனிக்கவும்."],
    tanglish: ["Public toilet problem: {sub}. Konjam paarunga."]
  },
  General: {
    en: ["{sub} {place}. Please take action.", "Complaint regarding {subl}; the issue has continued {dur}."],
    ta: ["{sub} தொடர்பாக புகார். நடவடிக்கை எடுக்கவும்."],
    tanglish: ["{sub} pathi complaint, {dur} solve aagala."]
  },
  "Tax and Licence": {
    en: ["{sub}: my request is pending {dur} without any reply."],
    ta: ["{sub} தொடர்பான பிரச்சினை. விரைவில் தீர்வு காணவும்."],
    tanglish: ["{sub} problem, {dur} reply varala."]
  },
  "Voter ID": {
    en: ["{sub}: application submitted but there has been no update {dur}."],
    ta: ["{sub} தொடர்பாக புகார். விரைவில் சரி செய்யவும்."],
    tanglish: ["{sub} pathi complaint, {dur} update illa."]
  },
  "Building Plan Permission": {
    en: ["Complaint regarding {subl} {place}."],
    ta: ["{sub} தொடர்பாக புகார். நடவடிக்கை எடுக்கவும்."],
    tanglish: ["{sub} pathi complaint, konjam paarunga."]
  },
  "Air Quality": {
    en: ["Dust and smoke from the construction site {place} is causing breathing problems.", "Air quality is very poor {place} due to burning of waste."],
    ta: ["கட்டுமான தூசியால் சுவாசிக்க சிரமமாக உள்ளது."],
    tanglish: ["Construction dust nala moochu vida kashtama irukku."]
  },
  MEGA: {
    en: ["{sub}. Residents {place} are affected by the project work.", "Regarding the mega streets project: {subl}. This has continued {dur}."],
    ta: ["மெகா ஸ்ட்ரீட் திட்டப் பணியில் பிரச்சினை: {sub}."],
    tanglish: ["Mega street project work la problem: {sub}. Konjam paarunga."]
  }
};

const GENERIC = {
  en: ["Complaint regarding {subl} {place}.", "{sub}: please take action {place}."],
  ta: ["{sub} தொடர்பாக புகார். நடவடிக்கை எடுக்கவும்."],
  tanglish: ["{sub} problem irukku engal area la, konjam seekiram paarunga."]
};

/** Short citizen-written titles, used for ~20% of complaints instead of the label. */
const SHORT_TITLES = {
  Garbage: { en: ["Garbage problem", "Garbage issue in our street"], ta: ["குப்பை பிரச்சினை"], tanglish: ["Kuppai problem"] },
  "Road and Footpath": { en: ["Road issue", "Problem on our road"], ta: ["சாலை பிரச்சினை"], tanglish: ["Road problem"] },
  "Street Light": { en: ["Street light problem"], ta: ["தெருவிளக்கு பிரச்சினை"], tanglish: ["Street light problem"] },
  "Public Health": { en: ["Health hazard in our area", "Sanitation problem"], ta: ["சுகாதார பிரச்சினை"], tanglish: ["Health problem"] },
  "Water Stagnation": { en: ["Drain problem", "Rain water drain issue"], ta: ["வடிகால் பிரச்சினை"], tanglish: ["Drain problem"] },
  "Storm Water Drains": { en: ["Unsafe drain work"], ta: ["வடிகால் பணி ஆபத்து"], tanglish: ["Drain work danger"] },
  Flood: { en: ["Flood help needed", "Urgent flood help"], ta: ["வெள்ள உதவி தேவை"], tanglish: ["Flood help venum"] },
  "Park and Playground": { en: ["Park issue"], ta: ["பூங்கா பிரச்சினை"], tanglish: ["Park problem"] },
  "Public Toilet": { en: ["Public toilet problem"], ta: ["கழிப்பறை பிரச்சினை"], tanglish: ["Toilet problem"] },
  General: { en: ["General complaint"], ta: ["பொது புகார்"], tanglish: ["General complaint"] },
  "Tax and Licence": { en: ["Tax issue"], ta: ["வரி பிரச்சினை"], tanglish: ["Tax problem"] },
  "Voter ID": { en: ["Voter ID issue"], ta: ["வாக்காளர் அட்டை பிரச்சினை"], tanglish: ["Voter ID problem"] },
  "Building Plan Permission": { en: ["Building issue"], ta: ["கட்டட பிரச்சினை"], tanglish: ["Building problem"] },
  "Air Quality": { en: ["Air pollution"], ta: ["காற்று மாசு"], tanglish: ["Pollution problem"] },
  MEGA: { en: ["Mega streets work issue"], ta: ["மெகா ஸ்ட்ரீட் பணி பிரச்சினை"], tanglish: ["Mega street problem"] }
};

/** Short titles that only fit one sub type; preferred over the category's. */
const SHORT_TITLES_BY_SUBTYPE = {
  "Removal of Garbage": { en: ["Garbage not cleared", "Waste dumped on road"], ta: ["குப்பை அகற்றப்படவில்லை"], tanglish: ["Kuppai edukala"] },
  "Pot hole fill up / Repairs to the damaged surface": { en: ["Pothole on main road", "Road damaged"], ta: ["சாலையில் பள்ளம்"], tanglish: ["Road la pallam"] },
  "Street Dogs": { en: ["Dog menace", "Stray dogs chasing people"], ta: ["தெருநாய் தொல்லை"], tanglish: ["Dog thollai"] },
  "Stagnation of Water": { en: ["Water stagnation", "Rain water not draining"], ta: ["மழைநீர் தேக்கம்"], tanglish: ["Thanni thengi nikkudhu"] },
  "Non burning of Street lights": { en: ["Street light not working", "Dark street at night"], ta: ["தெருவிளக்கு எரியவில்லை"], tanglish: ["Street light eriyala"] },
  "Mosquito Menace": { en: ["Mosquito problem"], ta: ["கொசுத் தொல்லை"], tanglish: ["Kosu thollai"] },
  "Removal of Fallen Trees": { en: ["Tree fallen on road"], ta: ["மரம் விழுந்துள்ளது"], tanglish: ["Maram vizhundhuduchu"] },
  "Repairs to existing Footpath": { en: ["Footpath broken"], ta: ["நடைபாதை உடைந்துள்ளது"], tanglish: ["Footpath odanjirukku"] }
};


/**
 * "Other" complaints, written so src/lib/ai-classifier.ts KEYWORD_MAP routes
 * each to `dept` (the generator re-checks every one at start-up). Substring
 * traps avoided on purpose: "street" contains "tree" (Parks), "parking" and
 * "parked" contain "park", "school" is matched before "school building".
 */
const OTHER = [
  { dept: "Bridges Department", title: "Subway problem", lang: "en", text: "The subway near the railway station has seepage and the lights inside are not working." },
  { dept: "Bridges Department", title: "Bridge unsafe", lang: "en", text: "Cracks have developed on the bridge over the canal; heavy vehicles still use it." },
  { dept: "Bridges Department", title: "Causeway unsafe", lang: "en", text: "The causeway near our area gets submerged and is unsafe to cross." },
  { dept: "Bridges Department", title: "Subway problem", lang: "tanglish", text: "Subway la thanni thengi nikkudhu, nadakka mudiyala." },
  { dept: "Bridges Department", title: "Subway lights", lang: "ta", text: "Subway-ல் விளக்குகள் எரியவில்லை, இரவில் நடக்க பயமாக உள்ளது." },
  { dept: "Education Department", title: "Drinking water in school", lang: "en", text: "Corporation school near our area has no drinking water facility for students." },
  { dept: "Education Department", title: "Mid-day meal quality", lang: "en", text: "The mid-day meal served to children in the corporation school is of poor quality." },
  { dept: "Education Department", title: "Teachers absent", lang: "en", text: "Teachers are not coming regularly to the corporation primary school." },
  { dept: "Education Department", title: "School toilet", lang: "tanglish", text: "Engal area corporation school la toilet illa, pasanga romba kashtapadranga." },
  { dept: "Education Department", title: "School drinking water", lang: "ta", text: "எங்கள் பகுதியில் உள்ள corporation school-ல் குடிநீர் வசதி இல்லை." },
  { dept: "Family Welfare Department", title: "Vaccine not available", lang: "en", text: "Immunization for my baby was due last week but the urban health post has no vaccine stock." },
  { dept: "Family Welfare Department", title: "Maternity assistance", lang: "en", text: "Maternity assistance amount not received even after four months." },
  { dept: "Family Welfare Department", title: "Vaccination camp", lang: "en", text: "Vaccination camp announced for our area was cancelled without notice." },
  { dept: "Buildings Department", title: "Community hall roof", lang: "en", text: "The community hall roof is leaking and plaster is falling on people during functions." },
  { dept: "Buildings Department", title: "Public convenience locked", lang: "en", text: "The public convenience near the market is locked and the structure is damaged." },
  { dept: "Buildings Department", title: "Community hall", lang: "tanglish", text: "Community hall la roof leak aagudhu, function nadakka mudiyala." },
  { dept: "Mechanical Engineering Department", title: "Abandoned vehicle", lang: "en", text: "A corporation vehicle is abandoned on our road for months and is leaking oil." },
  { dept: "Mechanical Engineering Department", title: "Lorry smoke", lang: "en", text: "Corporation lorry emits thick black smoke and makes loud noise near homes." },
  { dept: "Mechanical Engineering Department", title: "Noise at night", lang: "en", text: "Truck maintenance yard of the corporation is creating noise at night." },
  { dept: "Land & Estate Department", title: "Shop lease renewal", lang: "en", text: "The lease for the corporation shop allotted to us has not been renewed despite paying dues." },
  { dept: "Land & Estate Department", title: "Corporation land occupied", lang: "en", text: "Corporation land near the lake is being occupied by outsiders for storing materials." },
  { dept: "Land & Estate Department", title: "Rent revision", lang: "en", text: "Estate office has not responded to our request for shop rent revision." },
  { dept: "Council Department", title: "Councillor not reachable", lang: "en", text: "Request the ward councillor to hold a public meeting about local issues; councillor is not reachable." },
  { dept: "Council Department", title: "Petition to Mayor", lang: "en", text: "Petition to the Mayor regarding continued neglect of our locality." },
  { dept: "Council Department", title: "Council resolution", lang: "en", text: "The council meeting minutes on our resolution have not been shared with residents." },
  { dept: "Financial Management Unit", title: "Grant not paid", lang: "en", text: "Grant sanctioned for our residents welfare association has still not been paid out after a year." },
  { dept: "Financial Management Unit", title: "Loan not disbursed", lang: "en", text: "Loan under the corporation self-help group scheme is not disbursed." },
  { dept: "Financial Management Unit", title: "Bills pending", lang: "en", text: "Contractor bills for completed work are pending with the finance section." },
  { dept: "General Administration", title: "Office staff", lang: "en", text: "Staff at the zonal office are not available during office hours and are rude to the public." },
  { dept: "General Administration", title: "No response", lang: "en", text: "Administration at the ward office is not responding to letters for months." },
  { dept: "Revenue Department", title: "Noise at night", lang: "en", text: "Loud music from a marriage hall continues past midnight every weekend." },
  { dept: "Revenue Department", title: "Passage blocked", lang: "en", text: "Neighbour has blocked the common passage with a gate." },
  { dept: "Revenue Department", title: "Cattle shed nuisance", lang: "en", text: "A cattle shed in a residential area is causing smell and noise." },
  { dept: "Electrical Department", title: "No power at shelter", lang: "en", text: "No electricity in the corporation night shelter for three days." },
  { dept: "Health Department", title: "Market not cleaned", lang: "en", text: "Sanitation workers are not cleaning the market area." }
];

/** Share of Other complaints by target department (weights, not percentages). */
const OTHER_DEPT_WEIGHT = {
  "Bridges Department": 12, "Education Department": 14, "Family Welfare Department": 10,
  "Buildings Department": 10, "Mechanical Engineering Department": 8, "Land & Estate Department": 8,
  "Council Department": 8, "Financial Management Unit": 6, "General Administration": 8,
  "Revenue Department": 8, "Electrical Department": 4, "Health Department": 4
};

const JUNK_WORDS = ["test", "testing", "hi", "pls", "asdf", "ok", "help", "...", "xx", "no"];
const KEYBOARD = "asdfghjklqwertyuiopzxcvbnm";

// ----------------------------------------------------------------- people --

const NAMES = {
  Male: [
    "Murugan", "Senthil", "Karthik", "Suresh", "Ramesh", "Rajesh", "Kumar", "Vijay", "Arun", "Prakash",
    "Saravanan", "Selvam", "Ganesh", "Balaji", "Venkatesh", "Manikandan", "Dinesh", "Sathish", "Ravi",
    "Anand", "Gopal", "Mohan", "Siva", "Bharath", "Hari", "Karthikeyan", "Muthu", "Pandian", "Raja",
    "Sundar", "Elango", "Kathir", "Madhan", "Naveen", "Praveen", "Vignesh", "Abdul", "Joseph", "Imran", "Anthony"
  ],
  Female: [
    "Lakshmi", "Priya", "Divya", "Kavitha", "Meena", "Revathi", "Saranya", "Anitha", "Deepa", "Gayathri",
    "Janani", "Kalaiselvi", "Malathi", "Nandhini", "Pavithra", "Radha", "Sangeetha", "Sumathi", "Tamilselvi",
    "Uma", "Vasuki", "Valli", "Yamuna", "Bhuvana", "Chitra", "Geetha", "Indhu", "Jayanthi", "Kamala",
    "Mala", "Nithya", "Padma", "Rani", "Selvi", "Shanthi", "Vijayalakshmi", "Fathima", "Mary", "Ayesha", "Shalini"
  ],
  Transgender: ["Nila", "Aruna", "Kalki", "Shobana", "Tamizh", "Anjali"]
};

const LAST_NAMES = [
  "Kumar", "Raj", "Murugan", "Subramanian", "Krishnan", "Pandian", "Selvam", "Rajan", "Natarajan",
  "Ramasamy", "Sundaram", "Balasubramanian", "Arumugam", "Velu", "Durai", "Shankar", "Mani", "Babu",
  "Devi", "Kannan", "Perumal", "Srinivasan", "Venkatesan", "Govindan"
];

const INITIAL_LETTERS = "ABCDEGJKMNPRSTV";

// --------------------------------------------------------- typed streets --

const TYPED_STREET_BASES = [
  "gandhi nagar", "nehru", "kamarajar", "periyar", "anna", "bharathi", "vivekananda", "ambedkar",
  "m.g.r", "thiruvalluvar", "pillaiyar koil", "mariamman koil", "church", "masjid", "indira nagar",
  "rajaji", "vallalar", "subramania bharathi", "kalaignar", "sathya nagar", "teachers colony",
  "ganesh nagar", "lakshmi nagar", "sri ram nagar", "balaji nagar", "annai sathya", "muthamizh nagar",
  "kannadasan", "ellaiamman koil", "perumal koil", "vinayagar koil", "bajanai koil"
];
const TYPED_STREET_ORDINALS = ["", " 1st", " 2nd", " 3rd", " 4th", " 5th", " main", " cross", " 1st cross", " extn"];
const TYPED_STREET_SUFFIXES = ["street", "st", "road", "rd", "main road", "cross street", "lane"];
// The form's own street types (src/app/citizen/file-complaint/page.tsx), minus "Other".
const STREET_TYPES = ["Street", "Road", "Main Road", "Cross Street", "Avenue", "Lane", "Salai", "Nagar", "Colony", "Extension", "High Road", "Bazaar"];

// ---------------------------------------------------------------- remarks --

const IN_PROGRESS_REMARKS = {
  Garbage: ["Conservancy team deployed for clearance.", "Work order issued; conservancy team deployed."],
  "Street Light": ["Electrical maintenance team assigned; lamp replacement scheduled.", "Work order issued; team deployed."],
  "Road and Footpath": ["Work order issued; road gang deployed.", "Site inspected; patch work scheduled."],
  "Public Health": ["Sanitary Inspector inspected the site; action initiated.", "Field team deployed."],
  "Water Stagnation": ["Pump set deployed; desilting team assigned.", "Work order issued; team deployed."],
  "Storm Water Drains": ["Contractor instructed to barricade the work site.", "Work order issued; team deployed."],
  Flood: ["Relief team dispatched.", "Rescue and relief team deployed to the location."],
  "Park and Playground": ["Parks maintenance team deployed.", "Tree cutting team assigned."],
  _: ["Work order issued; team deployed.", "Field inspection done; work started.", "Assigned to field staff; work in progress."]
};

const COMPLETED_REMARKS = {
  "Pot hole fill up / Repairs to the damaged surface": ["Pothole patched with cold mix. Photo uploaded."],
  "Removal of Garbage": ["Garbage cleared and bleaching powder sprayed. Photo uploaded."],
  "Non burning of Street lights": ["Faulty lamp replaced; light working. Photo uploaded."],
  "Street Dogs": ["Dogs picked up by the ABC team for sterilisation and vaccination."],
  "Mosquito Menace": ["Fogging and larvicide spraying done in the street."],
  "Stagnation of Water": ["Water pumped out and drain inlet cleared. Photo uploaded."],
  "Removal of Fallen Trees": ["Fallen tree removed and branches cleared. Photo uploaded."],
  Garbage: ["Area cleaned and waste removed. Photo uploaded."],
  "Street Light": ["Street light repaired and tested. Photo uploaded."],
  "Road and Footpath": ["Repair work completed. Photo uploaded."],
  "Public Health": ["Action taken by the Sanitary Inspector. Photo uploaded."],
  "Water Stagnation": ["Drain desilted and flow restored. Photo uploaded."],
  "Public Toilet": ["Toilet cleaned and water supply restored. Photo uploaded."],
  Flood: ["Relief provided; water pumped out."],
  _: ["Work completed. Completion photo uploaded.", "Issue resolved. Photo uploaded."]
};

const VERIFIED_REMARKS = [
  "Verified with completion photo; closed.",
  "Field verification done; complainant confirmed; closed.",
  "Verified by the Collector's office; complaint closed."
];

const DO_REJECT_REASONS = [
  "Duplicate complaint; the issue is already being handled under another complaint.",
  "Not under GCC jurisdiction; to be taken up with TNEB (TANGEDCO).",
  "Not under GCC jurisdiction; to be taken up with Chennai Metrowater (CMWSSB).",
  "Not under GCC jurisdiction; the road is maintained by the State Highways Department.",
  "Insufficient details; the exact location could not be identified.",
  "The issue is on private property and outside the Corporation's scope."
];
const JUNK_REJECT_REASON = "Insufficient details to identify the issue or its location.";
const COLLECTOR_REJECT_REASONS = [
  "Completion photo does not match the reported location.",
  "Work not satisfactory on field inspection."
];

module.exports = {
  DUR, IMPACT, REPEAT, PLEA, LANDMARKS, OPENER, DETAIL, WHEN, EXTENT, DAYS, BY_SUBTYPE, BY_CATEGORY, GENERIC, SHORT_TITLES, SHORT_TITLES_BY_SUBTYPE,
  OTHER, OTHER_DEPT_WEIGHT, JUNK_WORDS, KEYBOARD, NAMES, LAST_NAMES, INITIAL_LETTERS,
  TYPED_STREET_BASES, TYPED_STREET_ORDINALS, TYPED_STREET_SUFFIXES, STREET_TYPES,
  IN_PROGRESS_REMARKS, COMPLETED_REMARKS, VERIFIED_REMARKS,
  DO_REJECT_REASONS, JUNK_REJECT_REASON, COLLECTOR_REJECT_REASONS
};
