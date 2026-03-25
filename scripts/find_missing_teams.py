"""Identify which Action Network names fail to resolve, and show closest DB match."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Team
from dk_ncaab.etl.normalize import resolve_team, normalize_team_name

# All 131 unique team names from the last scrape
AN_NAMES = [
    "Sac State", "N. Arizona", "Hawaii", "CS Northridge", "Middle Tenn",
    "W. Kentucky", "St. Thomas", "UMKC", "SD State", "Oral Roberts",
    "Virginia", "Ohio State", "Georgetown", "UConn", "Memphis",
    "Utah State", "Auburn", "Arkansas", "Texas", "Missouri",
    "Troy", "Southern Miss", "S. Carolina", "Alabama", "Marshall",
    "GA Southern", "Montana St", "Montana", "Cal Baptist", "Utah Tech",
    "Minnesota", "Washington", "UC Riverside", "UCSD", "CS Fullerton",
    "UC Irvine", "Nevada", "San Diego State", "Saint Mary's", "Pacific",
    "Gonzaga", "Santa Clara", "Wofford", "UNC Greensboro", "Grambling St",
    "Texas Southern", "MS Valley St", "Alabama A&M", "Bellarmine",
    "Austin Peay", "SFA", "UT Grande Valley", "Southern U", "Prairie View",
    "LSU", "Tennessee", "McNeese St", "E TA&M", "West Virginia", "UCF",
    "VCU", "Richmond", "Brown", "Dartmouth", "Columbia", "Princeton",
    "Cornell", "Penn", "Mississippi St", "Ole Miss", "Texas Tech",
    "Arizona", "Yale", "Harvard", "UCSB", "Cal Poly", "Tulsa",
    "Wichita State", "Samford", "E Tennessee St", "California",
    "Boston Col", "Clemson", "Duke", "G Tech", "Notre Dame", "TCU",
    "OK State", "Fordham", "Rhode Island", "Navy", "Colgate", "UCLA",
    "Michigan", "Texas A&M", "Vanderbilt", "Furman", "VMI", "Mercer",
    "Citadel", "UMBC", "New Hampshire", "Chicago St", "Dolphins",
    "Long Island", "New Haven", "St. John's", "Providence",
    "Northwestern", "Nebraska", "Kansas", "Iowa State", "Bucknell",
    "Boston U", "Liberty", "UTEP", "Stetson", "FGCU", "LA Tech", "FIU",
    "Wolves", "C. Arkansas", "Lakers", "St. Francis (PA)", "NDSU",
    "North Dakota", "Elon", "William & Mary", "Kent State", "Ball State",
    "Central Conn", "Fairleigh", "Bryant U", "Vermont", "W. Michigan",
    "E. Michigan", "Wagner", "Stonehill", "High Point", "Gardner-Webb",
    "Florida St", "VA Tech", "Pittsburgh", "UNC", "Presbyterian",
    "UNC Asheville", "SMU", "Syracuse", "E. Carolina", "Rice", "Army",
    "American U", "Charleston So", "Radford", "Villanova", "Creighton",
    "Kentucky", "Florida", "S. Alabama", "Arkansas St", "UL Monroe",
    "Texas St", "Delaware", "Missouri St", "Southern Utah", "UT-Arlington",
    "N.J.I.T.", "Maine", "USC Upstate", "Longwood", "Marquette", "Xavier",
    "Penn State", "Oregon", "Tenn St", "Morehead St", "Georgia State",
    "Old Dominion", "Georgia", "Oklahoma", "Florida A&M", "Jackson St",
    "App State", "JMU", "Tarleton St", "Ab Christian", "Lipscomb",
    "Queens", "North Florida", "Jacksonville", "Portland State",
    "N. Colorado", "Hofstra", "UNC Wilmington", "Albany", "Binghamton",
    "Miami (FL)", "NC State", "Stanford", "Wake Forest", "Louisville",
    "Baylor", "K State", "Houston", "Colorado", "BYU", "Duquesne",
    "St. Bonaventure", "B-Cookman", "Alcorn State", "AR-Pine Bluff",
    "Alabama St", "Wyoming", "Colorado St", "W. Carolina", "Chattanooga",
    "Ark-Little Rock", "E. Illinois", "SE Missouri", "Lindenwood",
    "TN-Martin", "SIU-Edwardsville", "New Orleans", "Hou Christian",
    "SE Louisiana", "NW State", "Lamar", "Texas A&M-CC", "Delaware St",
    "Norfolk State", "Coppin St", "NC Central", "MD-E Shore", "Howard",
    "Sam Houston", "Kennesaw St", "Weber State", "E. Washington",
    "Idaho State", "Idaho", "N. Mexico St", "Jax State", "LBSU",
    "UC Davis", "Tenn Tech", "Southern Indiana", "N. Illinois",
    "C. Michigan", "Toledo", "Bowling Green", "Nicholls St",
    "Incarnate Word", "Purdue", "Iowa", "Lehigh", "Lafayette",
    "Loyola Marymount", "Pepperdine", "Air Force", "Fresno State",
    "Grand Canyon", "San Jose St", "Morgan State",
]

session = SessionLocal()
found = 0
missing = []
for raw in sorted(set(AN_NAMES)):
    team = resolve_team(session, raw, "dknetwork")
    if team:
        found += 1
    else:
        norm = normalize_team_name(raw)
        missing.append((raw, norm))

print(f"Resolved: {found}/{len(set(AN_NAMES))}")
print(f"Missing: {len(missing)}")
print()
for raw, norm in sorted(missing, key=lambda x: x[1]):
    print(f"  '{raw:25s}' => '{norm}'")

session.close()
