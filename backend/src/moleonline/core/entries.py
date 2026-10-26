"""PDB entries and their assemblies, as requested: checked by the API, downloaded by the workers"""

import re

PREFERRED, ASYMMETRIC = "preferred", "asymmetric"
ASSEMBLY = re.compile(r"^[A-Za-z0-9]{1,10}$")  # goes into PDBe's URL
# 1tqn; a file name and part of PDBe's URL. Not the extended ids (pdb_00001tqn): PDBe's model server, OPM, M-CSA
# and the website's services know only these.
PDB_ID = re.compile(r"^[0-9][a-z0-9]{3}$", re.IGNORECASE)
