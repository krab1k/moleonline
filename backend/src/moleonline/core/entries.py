"""PDB entries and their assemblies, as requested: checked by the API, downloaded by the workers"""

import re

PREFERRED, ASYMMETRIC = "preferred", "asymmetric"
ASSEMBLY = re.compile(r"^[A-Za-z0-9]{1,10}$")  # goes into PDBe's URL
PDB_ID = re.compile(r"^[A-Za-z0-9_]{1,12}$")  # also extended ids (pdb_00001abc); a file name and part of PDBe's URL
