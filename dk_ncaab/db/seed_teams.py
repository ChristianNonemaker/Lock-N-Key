"""
Seed script — populates the teams + team_aliases tables with all
362 NCAA Division I men's basketball programs.

Each entry has a canonical name and a list of common aliases used by
various data sources (The-Odds-API, Action Network, ESPN, etc.).

Run with:  python -m dk_ncaab seed-teams
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import League, Team, TeamAlias
from dk_ncaab.etl.normalize import normalize_team_name

log = logging.getLogger(__name__)

# ── Curated team list ───────────────────────────────────────────
# Format: (canonical_name, [alias1, alias2, ...])
# Aliases are raw strings that will be normalized before insert.
# This covers the most common name variants across APIs.

_TEAMS: list[tuple[str, list[str]]] = [
    # --- A ---
    ("Abilene Christian", ["Abilene Christian", "Abilene Chr", "ACU Wildcats"]),
    ("Air Force", ["Air Force", "Air Force Falcons"]),
    ("Akron", ["Akron", "Akron Zips"]),
    ("Alabama", ["Alabama", "Alabama Crimson Tide", "Bama"]),
    ("Alabama A&M", ["Alabama A&M", "Alabama AM", "AAMU"]),
    ("Alabama State", ["Alabama State", "Alabama St", "Bama State"]),
    ("Albany", ["Albany", "UAlbany", "Albany Great Danes"]),
    ("Alcorn State", ["Alcorn State", "Alcorn St", "Alcorn"]),
    ("American", ["American", "American University", "American Eagles"]),
    ("Appalachian State", ["Appalachian State", "Appalachian St", "App State", "App St"]),
    ("Arizona", ["Arizona", "Arizona Wildcats"]),
    ("Arizona State", ["Arizona State", "Arizona St", "ASU"]),
    ("Arkansas", ["Arkansas", "Arkansas Razorbacks"]),
    ("Arkansas Pine Bluff", ["Arkansas Pine Bluff", "Ark Pine Bluff", "UAPB"]),
    ("Arkansas State", ["Arkansas State", "Arkansas St", "Ark State"]),
    ("Army", ["Army", "Army West Point", "Army Black Knights"]),
    ("Auburn", ["Auburn", "Auburn Tigers"]),
    ("Austin Peay", ["Austin Peay", "Austin Peay State", "APSU"]),
    # --- B ---
    ("Ball State", ["Ball State", "Ball St", "Ball State Cardinals"]),
    ("Baylor", ["Baylor", "Baylor Bears"]),
    ("Bellarmine", ["Bellarmine", "Bellarmine Knights"]),
    ("Belmont", ["Belmont", "Belmont Bruins"]),
    ("Bethune-Cookman", ["Bethune-Cookman", "Bethune Cookman", "B-CU"]),
    ("Binghamton", ["Binghamton", "Binghamton Bearcats"]),
    ("Boise State", ["Boise State", "Boise St", "Boise State Broncos"]),
    ("Boston College", ["Boston College", "BC", "Boston College Eagles"]),
    ("Boston University", ["Boston University", "Boston U", "BU Terriers"]),
    ("Bowling Green", ["Bowling Green", "Bowling Green State", "BGSU"]),
    ("Bradley", ["Bradley", "Bradley Braves"]),
    ("Brown", ["Brown", "Brown Bears"]),
    ("Bryant", ["Bryant", "Bryant Bulldogs"]),
    ("Bucknell", ["Bucknell", "Bucknell Bison"]),
    ("Buffalo", ["Buffalo", "Buffalo Bulls", "UB"]),
    ("Butler", ["Butler", "Butler Bulldogs"]),
    # --- C ---
    ("Cal Poly", ["Cal Poly", "Cal Poly SLO", "Cal Poly Mustangs"]),
    ("Cal State Bakersfield", ["Cal State Bakersfield", "CSU Bakersfield", "CSUB"]),
    ("Cal State Fullerton", ["Cal State Fullerton", "CSU Fullerton", "CSUF", "CS Fullerton"]),
    ("Cal State Northridge", ["Cal State Northridge", "CSU Northridge", "CSUN"]),
    ("California", ["California", "Cal", "Cal Bears", "California Golden Bears"]),
    ("California Baptist", ["California Baptist", "Cal Baptist", "CBU"]),
    ("Campbell", ["Campbell", "Campbell Fighting Camels"]),
    ("Canisius", ["Canisius", "Canisius Golden Griffins"]),
    ("Central Arkansas", ["Central Arkansas", "UCA", "Central Ark"]),
    ("Central Connecticut", ["Central Connecticut", "Central Conn", "CCSU", "Central Connecticut State"]),
    ("Central Michigan", ["Central Michigan", "Central Mich", "CMU Chippewas"]),
    ("Charleston", ["Charleston", "College of Charleston", "CofC", "Charleston Cougars"]),
    ("Charleston Southern", ["Charleston Southern", "Charleston So", "CSU Buccaneers"]),
    ("Charlotte", ["Charlotte", "UNC Charlotte", "UNCC", "Charlotte 49ers"]),
    ("Chattanooga", ["Chattanooga", "UT Chattanooga", "UTC", "Chattanooga Mocs"]),
    ("Chicago State", ["Chicago State", "Chicago St"]),
    ("Cincinnati", ["Cincinnati", "Cincy", "UC Bearcats", "Cincinnati Bearcats"]),
    ("Clemson", ["Clemson", "Clemson Tigers"]),
    ("Cleveland State", ["Cleveland State", "Cleveland St"]),
    ("Coastal Carolina", ["Coastal Carolina", "Coastal", "CCU"]),
    ("Colgate", ["Colgate", "Colgate Raiders"]),
    ("Colorado", ["Colorado", "Colorado Buffaloes", "CU Buffs"]),
    ("Colorado State", ["Colorado State", "Colorado St", "CSU Rams"]),
    ("Columbia", ["Columbia", "Columbia Lions"]),
    ("Connecticut", ["Connecticut", "UConn", "Conn", "Connecticut Huskies"]),
    ("Coppin State", ["Coppin State", "Coppin St", "Coppin"]),
    ("Cornell", ["Cornell", "Cornell Big Red"]),
    ("Creighton", ["Creighton", "Creighton Bluejays"]),
    # --- D ---
    ("Dartmouth", ["Dartmouth", "Dartmouth Big Green"]),
    ("Davidson", ["Davidson", "Davidson Wildcats"]),
    ("Dayton", ["Dayton", "Dayton Flyers"]),
    ("Delaware", ["Delaware", "Delaware Fightin Blue Hens", "UD"]),
    ("Delaware State", ["Delaware State", "Delaware St", "Del State"]),
    ("Denver", ["Denver", "Denver Pioneers"]),
    ("DePaul", ["DePaul", "De Paul", "DePaul Blue Demons"]),
    ("Detroit Mercy", ["Detroit Mercy", "Detroit", "UDM"]),
    ("Drake", ["Drake", "Drake Bulldogs"]),
    ("Drexel", ["Drexel", "Drexel Dragons"]),
    ("Duke", ["Duke", "Duke Blue Devils"]),
    ("Duquesne", ["Duquesne", "Duquesne Dukes"]),
    # --- E ---
    ("East Carolina", ["East Carolina", "ECU", "East Carolina Pirates"]),
    ("East Tennessee State", ["East Tennessee State", "ETSU", "East Tenn St", "East Tennessee St"]),
    ("Eastern Illinois", ["Eastern Illinois", "Eastern Ill", "EIU"]),
    ("Eastern Kentucky", ["Eastern Kentucky", "Eastern Ky", "EKU"]),
    ("Eastern Michigan", ["Eastern Michigan", "Eastern Mich", "EMU"]),
    ("Eastern Washington", ["Eastern Washington", "Eastern Wash", "EWU"]),
    ("Elon", ["Elon", "Elon Phoenix"]),
    ("Evansville", ["Evansville", "UE Aces"]),
    # --- F ---
    ("Fairfield", ["Fairfield", "Fairfield Stags"]),
    ("Fairleigh Dickinson", ["Fairleigh Dickinson", "FDU", "Fairleigh Dick"]),
    ("Florida", ["Florida", "Florida Gators", "UF"]),
    ("Florida A&M", ["Florida A&M", "Florida AM", "FAMU"]),
    ("Florida Atlantic", ["Florida Atlantic", "FAU", "Fla Atlantic", "Florida Atlantic Owls"]),
    ("Florida Gulf Coast", ["Florida Gulf Coast", "FGCU", "Fla Gulf Coast"]),
    ("Florida International", ["Florida International", "FIU", "Fla International"]),
    ("Florida State", ["Florida State", "Florida St", "FSU", "Florida State Seminoles"]),
    ("Fordham", ["Fordham", "Fordham Rams"]),
    ("Fresno State", ["Fresno State", "Fresno St", "Fresno State Bulldogs"]),
    ("Furman", ["Furman", "Furman Paladins"]),
    # --- G ---
    ("Gardner-Webb", ["Gardner-Webb", "Gardner Webb", "GWU"]),
    ("George Mason", ["George Mason", "GMU", "George Mason Patriots"]),
    ("George Washington", ["George Washington", "GW", "George Washington Revolutionaries"]),
    ("Georgetown", ["Georgetown", "Georgetown Hoyas"]),
    ("Georgia", ["Georgia", "Georgia Bulldogs", "UGA"]),
    ("Georgia Southern", ["Georgia Southern", "Ga Southern", "Georgia So"]),
    ("Georgia State", ["Georgia State", "Georgia St", "Ga State"]),
    ("Georgia Tech", ["Georgia Tech", "Ga Tech", "Georgia Tech Yellow Jackets", "GT"]),
    ("Gonzaga", ["Gonzaga", "Gonzaga Bulldogs"]),
    ("Grambling State", ["Grambling State", "Grambling", "Grambling St"]),
    ("Grand Canyon", ["Grand Canyon", "GCU", "Grand Canyon Antelopes"]),
    ("Green Bay", ["Green Bay", "UW Green Bay", "UWGB"]),
    # --- H ---
    ("Hampton", ["Hampton", "Hampton Pirates"]),
    ("Hartford", ["Hartford", "Hartford Hawks"]),
    ("Harvard", ["Harvard", "Harvard Crimson"]),
    ("Hawaii", ["Hawaii", "Hawai'i", "Hawaii Rainbow Warriors"]),
    ("High Point", ["High Point", "High Point Panthers"]),
    ("Hofstra", ["Hofstra", "Hofstra Pride"]),
    ("Holy Cross", ["Holy Cross", "Holy Cross Crusaders"]),
    ("Houston", ["Houston", "Houston Cougars", "UH"]),
    ("Houston Christian", ["Houston Christian", "Houston Baptist", "HCU", "HBU"]),
    ("Howard", ["Howard", "Howard Bison"]),
    # --- I ---
    ("Idaho", ["Idaho", "Idaho Vandals"]),
    ("Idaho State", ["Idaho State", "Idaho St"]),
    ("Illinois", ["Illinois", "Illinois Fighting Illini", "U of I"]),
    ("Illinois State", ["Illinois State", "Illinois St", "ISU Redbirds"]),
    ("Incarnate Word", ["Incarnate Word", "UIW", "UIWA"]),
    ("Indiana", ["Indiana", "Indiana Hoosiers", "IU"]),
    ("Indiana State", ["Indiana State", "Indiana St"]),
    ("Iona", ["Iona", "Iona Gaels"]),
    ("Iowa", ["Iowa", "Iowa Hawkeyes"]),
    ("Iowa State", ["Iowa State", "Iowa St", "ISU Cyclones"]),
    ("IUPUI", ["IUPUI", "IU Indianapolis"]),
    # --- J ---
    ("Jackson State", ["Jackson State", "Jackson St"]),
    ("Jacksonville", ["Jacksonville", "JU Dolphins"]),
    ("Jacksonville State", ["Jacksonville State", "Jacksonville St", "Jax State"]),
    ("James Madison", ["James Madison", "JMU", "James Madison Dukes"]),
    # --- K ---
    ("Kansas", ["Kansas", "Kansas Jayhawks", "KU"]),
    ("Kansas City", ["Kansas City", "UMKC", "Kansas City Roos"]),
    ("Kansas State", ["Kansas State", "Kansas St", "K-State"]),
    ("Kennesaw State", ["Kennesaw State", "Kennesaw St", "Kennesaw", "KSU Owls"]),
    ("Kent State", ["Kent State", "Kent St", "Kent State Golden Flashes"]),
    ("Kentucky", ["Kentucky", "Kentucky Wildcats", "UK"]),
    # --- L ---
    ("La Salle", ["La Salle", "LaSalle"]),
    ("Lafayette", ["Lafayette", "Lafayette Leopards"]),
    ("Lamar", ["Lamar", "Lamar Cardinals"]),
    ("Le Moyne", ["Le Moyne", "LeMoyne"]),
    ("Lehigh", ["Lehigh", "Lehigh Mountain Hawks"]),
    ("Liberty", ["Liberty", "Liberty Flames"]),
    ("Lindenwood", ["Lindenwood", "Lindenwood Lions"]),
    ("Lipscomb", ["Lipscomb", "Lipscomb Bisons"]),
    ("Little Rock", ["Little Rock", "UALR", "Arkansas Little Rock"]),
    ("Long Beach State", ["Long Beach State", "Long Beach St", "LBSU"]),
    ("Long Island", ["Long Island", "LIU", "Long Island University"]),
    ("Longwood", ["Longwood", "Longwood Lancers"]),
    ("Louisiana", ["Louisiana", "Louisiana Ragin Cajuns", "UL Lafayette", "Louisiana-Lafayette"]),
    ("Louisiana Monroe", ["Louisiana Monroe", "UL Monroe", "ULM", "Louisiana-Monroe"]),
    ("Louisiana Tech", ["Louisiana Tech", "La Tech"]),
    ("Louisville", ["Louisville", "Louisville Cardinals", "UofL"]),
    ("Loyola Chicago", ["Loyola Chicago", "Loyola-Chicago", "Loyola IL", "Loyola Ramblers"]),
    ("Loyola Marymount", ["Loyola Marymount", "LMU", "Loyola-Marymount"]),
    ("Loyola Maryland", ["Loyola Maryland", "Loyola-Maryland", "Loyola MD"]),
    ("LSU", ["LSU", "Louisiana State", "LSU Tigers"]),
    # --- M ---
    ("Maine", ["Maine", "Maine Black Bears"]),
    ("Manhattan", ["Manhattan", "Manhattan Jaspers"]),
    ("Marist", ["Marist", "Marist Red Foxes"]),
    ("Marquette", ["Marquette", "Marquette Golden Eagles"]),
    ("Marshall", ["Marshall", "Marshall Thundering Herd"]),
    ("Maryland", ["Maryland", "Maryland Terrapins", "UMD"]),
    ("Maryland Eastern Shore", ["Maryland Eastern Shore", "MD Eastern Shore", "UMES"]),
    ("Massachusetts", ["Massachusetts", "UMass", "UMass Minutemen"]),
    ("McNeese", ["McNeese", "McNeese State", "McNeese St"]),
    ("Memphis", ["Memphis", "Memphis Tigers"]),
    ("Mercer", ["Mercer", "Mercer Bears"]),
    ("Merrimack", ["Merrimack", "Merrimack Warriors"]),
    ("Miami FL", ["Miami FL", "Miami (FL)", "Miami", "Miami Hurricanes"]),
    ("Miami OH", ["Miami OH", "Miami (OH)", "Miami Ohio", "Miami RedHawks"]),
    ("Michigan", ["Michigan", "Michigan Wolverines"]),
    ("Michigan State", ["Michigan State", "Michigan St", "MSU Spartans"]),
    ("Middle Tennessee", ["Middle Tennessee", "MTSU", "Middle Tenn", "Middle Tennessee State"]),
    ("Milwaukee", ["Milwaukee", "UW Milwaukee", "UWM"]),
    ("Minnesota", ["Minnesota", "Minnesota Golden Gophers"]),
    ("Mississippi State", ["Mississippi State", "Mississippi St", "Miss State", "MSU Bulldogs"]),
    ("Mississippi Valley State", ["Mississippi Valley State", "MVSU", "Miss Valley St"]),
    ("Missouri", ["Missouri", "Mizzou", "Missouri Tigers"]),
    ("Missouri State", ["Missouri State", "Missouri St"]),
    ("Monmouth", ["Monmouth", "Monmouth Hawks"]),
    ("Montana", ["Montana", "Montana Grizzlies"]),
    ("Montana State", ["Montana State", "Montana St"]),
    ("Morehead State", ["Morehead State", "Morehead St"]),
    ("Morgan State", ["Morgan State", "Morgan St"]),
    ("Mount St. Mary's", ["Mount St. Mary's", "Mt St Marys", "Mount St Marys", "The Mount"]),
    ("Murray State", ["Murray State", "Murray St"]),
    # --- N ---
    ("Navy", ["Navy", "Navy Midshipmen"]),
    ("Nebraska", ["Nebraska", "Nebraska Cornhuskers"]),
    ("Nevada", ["Nevada", "Nevada Wolf Pack"]),
    ("New Hampshire", ["New Hampshire", "UNH"]),
    ("New Mexico", ["New Mexico", "UNM", "New Mexico Lobos"]),
    ("New Mexico State", ["New Mexico State", "New Mexico St", "NMSU"]),
    ("New Orleans", ["New Orleans", "UNO Privateers"]),
    ("Niagara", ["Niagara", "Niagara Purple Eagles"]),
    ("Nicholls", ["Nicholls", "Nicholls State", "Nicholls St"]),
    ("NJIT", ["NJIT", "New Jersey Tech"]),
    ("Norfolk State", ["Norfolk State", "Norfolk St"]),
    ("North Alabama", ["North Alabama", "UNA"]),
    ("North Carolina", ["North Carolina", "UNC", "North Carolina Tar Heels", "NC"]),
    ("North Carolina A&T", ["North Carolina A&T", "NC A&T", "NC AT", "NCA&T"]),
    ("North Carolina Central", ["North Carolina Central", "NC Central", "NCCU"]),
    ("North Carolina State", ["North Carolina State", "NC State", "N.C. State", "NCSU"]),
    ("North Dakota", ["North Dakota", "UND", "North Dakota Fighting Hawks"]),
    ("North Dakota State", ["North Dakota State", "North Dakota St", "NDSU"]),
    ("North Florida", ["North Florida", "UNF", "North Florida Ospreys"]),
    ("North Texas", ["North Texas", "UNT", "North Texas Mean Green"]),
    ("Northeastern", ["Northeastern", "Northeastern Huskies"]),
    ("Northern Arizona", ["Northern Arizona", "NAU"]),
    ("Northern Colorado", ["Northern Colorado", "UNC Bears", "Northern Colo"]),
    ("Northern Illinois", ["Northern Illinois", "Northern Ill", "NIU"]),
    ("Northern Iowa", ["Northern Iowa", "UNI", "Northern Iowa Panthers"]),
    ("Northern Kentucky", ["Northern Kentucky", "NKU", "Northern Ky"]),
    ("Northwestern", ["Northwestern", "Northwestern Wildcats"]),
    ("Northwestern State", ["Northwestern State", "Northwestern St", "NSU Demons"]),
    ("Notre Dame", ["Notre Dame", "Notre Dame Fighting Irish"]),
    # --- O ---
    ("Oakland", ["Oakland", "Oakland Golden Grizzlies"]),
    ("Ohio", ["Ohio", "Ohio Bobcats", "Ohio University"]),
    ("Ohio State", ["Ohio State", "Ohio St", "OSU Buckeyes"]),
    ("Oklahoma", ["Oklahoma", "Oklahoma Sooners", "OU"]),
    ("Oklahoma State", ["Oklahoma State", "Oklahoma St", "OSU Cowboys", "OK State"]),
    ("Old Dominion", ["Old Dominion", "ODU"]),
    ("Ole Miss", ["Ole Miss", "Mississippi", "Ole Miss Rebels"]),
    ("Omaha", ["Omaha", "Nebraska Omaha", "UNO"]),
    ("Oral Roberts", ["Oral Roberts", "ORU"]),
    ("Oregon", ["Oregon", "Oregon Ducks"]),
    ("Oregon State", ["Oregon State", "Oregon St"]),
    # --- P ---
    ("Pacific", ["Pacific", "Pacific Tigers"]),
    ("Penn", ["Penn", "Pennsylvania", "Penn Quakers"]),
    ("Penn State", ["Penn State", "Penn St", "PSU Nittany Lions"]),
    ("Pepperdine", ["Pepperdine", "Pepperdine Waves"]),
    ("Pittsburgh", ["Pittsburgh", "Pitt", "Pitt Panthers"]),
    ("Portland", ["Portland", "Portland Pilots"]),
    ("Portland State", ["Portland State", "Portland St"]),
    ("Prairie View A&M", ["Prairie View A&M", "Prairie View", "PVAMU"]),
    ("Presbyterian", ["Presbyterian", "Presbyterian Blue Hose"]),
    ("Princeton", ["Princeton", "Princeton Tigers"]),
    ("Providence", ["Providence", "Providence Friars"]),
    ("Purdue", ["Purdue", "Purdue Boilermakers"]),
    ("Purdue Fort Wayne", ["Purdue Fort Wayne", "PFW", "Fort Wayne", "IPFW"]),
    # --- Q ---
    ("Queens", ["Queens", "Queens University", "Queens Royals"]),
    ("Quinnipiac", ["Quinnipiac", "Quinnipiac Bobcats"]),
    # --- R ---
    ("Radford", ["Radford", "Radford Highlanders"]),
    ("Rhode Island", ["Rhode Island", "URI", "Rhody"]),
    ("Rice", ["Rice", "Rice Owls"]),
    ("Richmond", ["Richmond", "Richmond Spiders"]),
    ("Rider", ["Rider", "Rider Broncs"]),
    ("Robert Morris", ["Robert Morris", "RMU"]),
    ("Rutgers", ["Rutgers", "Rutgers Scarlet Knights"]),
    # --- S ---
    ("Sacramento State", ["Sacramento State", "Sacramento St", "Sac State"]),
    ("Sacred Heart", ["Sacred Heart", "SHU Pioneers"]),
    ("Saint Francis", ["Saint Francis", "St Francis", "St. Francis PA"]),
    ("Saint Joseph's", ["Saint Joseph's", "St Joseph's", "St. Joseph's", "Saint Josephs"]),
    ("Saint Louis", ["Saint Louis", "St Louis", "St. Louis", "SLU", "Saint Louis Billikens"]),
    ("Saint Mary's", ["Saint Mary's", "St Mary's", "St. Mary's", "Saint Marys", "SMC Gaels"]),
    ("Saint Peter's", ["Saint Peter's", "St Peter's", "St. Peter's", "Saint Peters"]),
    ("Sam Houston", ["Sam Houston", "Sam Houston State", "Sam Houston St", "SHSU"]),
    ("Samford", ["Samford", "Samford Bulldogs"]),
    ("San Diego", ["San Diego", "San Diego Toreros"]),
    ("San Diego State", ["San Diego State", "San Diego St", "SDSU"]),
    ("San Francisco", ["San Francisco", "USF Dons", "San Francisco Dons"]),
    ("San Jose State", ["San Jose State", "San Jose St", "SJSU"]),
    ("Santa Clara", ["Santa Clara", "Santa Clara Broncos"]),
    ("Seattle", ["Seattle", "Seattle University", "Seattle Redhawks"]),
    ("Seton Hall", ["Seton Hall", "Seton Hall Pirates"]),
    ("Siena", ["Siena", "Siena Saints"]),
    ("SIU Edwardsville", ["SIU Edwardsville", "SIUE", "Southern Illinois Edwardsville"]),
    ("SMU", ["SMU", "Southern Methodist", "SMU Mustangs"]),
    ("South Alabama", ["South Alabama", "South Ala", "USA Jaguars"]),
    ("South Carolina", ["South Carolina", "USC Gamecocks", "S Carolina", "South Carolina Gamecocks"]),
    ("South Carolina State", ["South Carolina State", "SC State", "South Carolina St"]),
    ("South Dakota", ["South Dakota", "USD Coyotes"]),
    ("South Dakota State", ["South Dakota State", "South Dakota St", "SDSU Jackrabbits"]),
    ("South Florida", ["South Florida", "USF", "USF Bulls"]),
    ("Southeast Missouri State", ["Southeast Missouri State", "SE Missouri St", "SEMO"]),
    ("Southeastern Louisiana", ["Southeastern Louisiana", "SE Louisiana", "SLU Lions"]),
    ("Southern", ["Southern", "Southern University", "Southern Jaguars"]),
    ("Southern Illinois", ["Southern Illinois", "Southern Ill", "SIU"]),
    ("Southern Indiana", ["Southern Indiana", "USI"]),
    ("Southern Miss", ["Southern Miss", "Southern Mississippi", "USM"]),
    ("Southern Utah", ["Southern Utah", "SUU"]),
    ("St. Bonaventure", ["St. Bonaventure", "St Bonaventure", "Saint Bonaventure", "Bonnies"]),
    ("St. John's", ["St. John's", "St Johns", "Saint Johns", "St. John's Red Storm"]),
    ("St. Thomas", ["St. Thomas", "St Thomas", "Saint Thomas MN"]),
    ("Stanford", ["Stanford", "Stanford Cardinal"]),
    ("Stephen F. Austin", ["Stephen F. Austin", "SFA", "SF Austin", "Stephen F Austin"]),
    ("Stetson", ["Stetson", "Stetson Hatters"]),
    ("Stonehill", ["Stonehill", "Stonehill Skyhawks"]),
    ("Stony Brook", ["Stony Brook", "Stony Brook Seawolves"]),
    ("Syracuse", ["Syracuse", "Syracuse Orange", "Cuse"]),
    # --- T ---
    ("Tarleton State", ["Tarleton State", "Tarleton", "Tarleton St"]),
    ("TCU", ["TCU", "Texas Christian", "TCU Horned Frogs"]),
    ("Temple", ["Temple", "Temple Owls"]),
    ("Tennessee", ["Tennessee", "Tennessee Volunteers", "Vols"]),
    ("Tennessee State", ["Tennessee State", "Tennessee St"]),
    ("Tennessee Tech", ["Tennessee Tech", "Tenn Tech", "TTU Golden Eagles"]),
    ("Texas", ["Texas", "Texas Longhorns", "UT"]),
    ("Texas A&M", ["Texas A&M", "Texas AM", "TAMU", "Texas A&M Aggies"]),
    ("Texas A&M Corpus Christi", ["Texas A&M Corpus Christi", "TAMUCC", "A&M Corpus Christi"]),
    ("Texas Southern", ["Texas Southern", "Texas So", "TSU Tigers"]),
    ("Texas State", ["Texas State", "Texas St", "Texas State Bobcats"]),
    ("Texas Tech", ["Texas Tech", "Texas Tech Red Raiders", "TTU"]),
    ("The Citadel", ["The Citadel", "Citadel", "Citadel Bulldogs"]),
    ("Toledo", ["Toledo", "Toledo Rockets"]),
    ("Towson", ["Towson", "Towson Tigers"]),
    ("Troy", ["Troy", "Troy Trojans"]),
    ("Tulane", ["Tulane", "Tulane Green Wave"]),
    ("Tulsa", ["Tulsa", "Tulsa Golden Hurricane"]),
    # --- U ---
    ("UAB", ["UAB", "Alabama Birmingham", "UAB Blazers"]),
    ("UC Davis", ["UC Davis", "California Davis"]),
    ("UC Irvine", ["UC Irvine", "California Irvine", "UCI"]),
    ("UC Riverside", ["UC Riverside", "California Riverside", "UCR"]),
    ("UC San Diego", ["UC San Diego", "California San Diego", "UCSD"]),
    ("UC Santa Barbara", ["UC Santa Barbara", "UCSB", "California Santa Barbara"]),
    ("UCF", ["UCF", "Central Florida", "UCF Knights"]),
    ("UCLA", ["UCLA", "UCLA Bruins"]),
    ("UMass Lowell", ["UMass Lowell", "Massachusetts Lowell"]),
    ("UMBC", ["UMBC", "Maryland Baltimore County"]),
    ("UNC Asheville", ["UNC Asheville", "UNCA", "North Carolina Asheville"]),
    ("UNC Greensboro", ["UNC Greensboro", "UNCG", "North Carolina Greensboro"]),
    ("UNC Wilmington", ["UNC Wilmington", "UNCW", "North Carolina Wilmington"]),
    ("UNLV", ["UNLV", "Nevada Las Vegas", "UNLV Rebels"]),
    ("USC", ["USC", "Southern California", "USC Trojans"]),
    ("USC Upstate", ["USC Upstate", "South Carolina Upstate"]),
    ("UT Arlington", ["UT Arlington", "Texas Arlington", "UTA"]),
    ("UT Martin", ["UT Martin", "Tennessee Martin", "UTM"]),
    ("UT Rio Grande Valley", ["UT Rio Grande Valley", "UTRGV", "Texas Rio Grande Valley"]),
    ("Utah", ["Utah", "Utah Utes"]),
    ("Utah State", ["Utah State", "Utah St", "USU Aggies"]),
    ("Utah Tech", ["Utah Tech", "Dixie State"]),
    ("Utah Valley", ["Utah Valley", "UVU"]),
    ("UTEP", ["UTEP", "Texas El Paso", "UTEP Miners"]),
    ("UTSA", ["UTSA", "Texas San Antonio", "UTSA Roadrunners"]),
    # --- V ---
    ("Valparaiso", ["Valparaiso", "Valpo"]),
    ("Vanderbilt", ["Vanderbilt", "Vanderbilt Commodores", "Vandy"]),
    ("VCU", ["VCU", "Virginia Commonwealth", "VCU Rams"]),
    ("Vermont", ["Vermont", "Vermont Catamounts"]),
    ("Villanova", ["Villanova", "Villanova Wildcats", "Nova"]),
    ("Virginia", ["Virginia", "Virginia Cavaliers", "UVA"]),
    ("Virginia Tech", ["Virginia Tech", "Va Tech", "VT Hokies"]),
    ("VMI", ["VMI", "Virginia Military", "VMI Keydets"]),
    # --- W ---
    ("Wagner", ["Wagner", "Wagner Seahawks"]),
    ("Wake Forest", ["Wake Forest", "Wake", "Wake Forest Demon Deacons"]),
    ("Washington", ["Washington", "Washington Huskies", "UW"]),
    ("Washington State", ["Washington State", "Washington St", "Wazzu", "WSU"]),
    ("Weber State", ["Weber State", "Weber St"]),
    ("West Virginia", ["West Virginia", "WVU", "West Virginia Mountaineers"]),
    ("Western Carolina", ["Western Carolina", "Western Car", "WCU"]),
    ("Western Illinois", ["Western Illinois", "Western Ill", "WIU"]),
    ("Western Kentucky", ["Western Kentucky", "Western Ky", "WKU"]),
    ("Western Michigan", ["Western Michigan", "Western Mich", "WMU"]),
    ("Wichita State", ["Wichita State", "Wichita St", "Wichita State Shockers"]),
    ("William & Mary", ["William & Mary", "William and Mary", "W&M"]),
    ("Winthrop", ["Winthrop", "Winthrop Eagles"]),
    ("Wisconsin", ["Wisconsin", "Wisconsin Badgers"]),
    ("Wofford", ["Wofford", "Wofford Terriers"]),
    ("Wright State", ["Wright State", "Wright St"]),
    ("Wyoming", ["Wyoming", "Wyoming Cowboys"]),
    # --- X ---
    ("Xavier", ["Xavier", "Xavier Musketeers"]),
    # --- Y ---
    ("Yale", ["Yale", "Yale Bulldogs"]),
    ("Youngstown State", ["Youngstown State", "Youngstown St", "YSU"]),
]


# ── Seeding logic ──────────────────────────────────────────────

def seed_teams(session: Session | None = None) -> int:
    """
    Insert teams and aliases. Skips any that already exist.
    Returns count of newly-created teams.
    """
    own_session = session is None
    if own_session:
        session = SessionLocal()

    try:
        # Ensure league exists
        league = session.query(League).filter_by(key="ncaab").first()
        if not league:
            league = League(key="ncaab", name="NCAA Men's Basketball")
            session.add(league)
            session.flush()

        created = 0
        for canonical, aliases in _TEAMS:
            norm = normalize_team_name(canonical)
            existing = session.query(Team).filter_by(
                league_id=league.id, normalized_name=norm
            ).first()

            if existing:
                team = existing
            else:
                team = Team(
                    league_id=league.id,
                    name=canonical,
                    normalized_name=norm,
                )
                session.add(team)
                session.flush()
                created += 1

            # Insert aliases (skip duplicates)
            for alias_raw in aliases:
                alias_norm = normalize_team_name(alias_raw)
                exists = session.query(TeamAlias).filter_by(
                    team_id=team.id, alias=alias_norm, source="seed"
                ).first()
                if not exists:
                    session.add(TeamAlias(
                        team_id=team.id, alias=alias_norm, source="seed"
                    ))

        session.commit()
        log.info("Seeded %d new teams (%d total in list)", created, len(_TEAMS))
        return created

    finally:
        if own_session:
            session.close()
