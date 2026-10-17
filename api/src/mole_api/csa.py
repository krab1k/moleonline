"""Active sites per PDB entry from M-CSA (resources/mcsa_sites.csv.gz, made by tools/mcsa/convert.py): one site per
copy, residues by the author's chain and number. /CSA offers them as tunnel starting points."""

import csv
import gzip
import itertools
from pathlib import Path

from pydantic import BaseModel

FILE = Path(__file__).parent / "resources" / "mcsa_sites.csv.gz"


class CSAResidue(BaseModel):
    Name: str
    Chain: str
    SequenceNumber: int


class CSA:
    """Each entry's lines of the CSV, parsed when asked for: about 15 MB instead of an object per residue"""

    def __init__(self, path: Path = FILE):
        """path: gzipped CSV "pdb,site,chain,residue,number", sorted by entry and site, sites numbered from 1"""
        with gzip.open(path, "rt") as rows:
            next(rows)  # the header
            self.entries = {pdb: "".join(lines) for pdb, lines in itertools.groupby(rows, lambda line: line[:line.index(",")])}

    def get(self, pdb_id: str) -> list[list[CSAResidue]]:
        """The active sites of a PDB entry (lower case id), none when M-CSA does not know it"""
        rows = csv.reader(self.entries.get(pdb_id, "").splitlines())
        return [[CSAResidue(Chain=chain, Name=name, SequenceNumber=int(number)) for _, _, chain, name, number in site]
                for _, site in itertools.groupby(rows, lambda row: row[1])]
