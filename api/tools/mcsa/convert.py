"""Active sites per PDB entry from M-CSA, the data of /CSA (tunnel starting points in the web).

usage: python3 -I convert.py <homologues_residues.json> <mcsa_sites.csv.gz>

Input: M-CSA's catalytic residues mapped to PDB entries, https://www.ebi.ac.uk/thornton-srv/m-csa/api/homologues_residues.json
(about 340 MB, downloaded 2026-10-16; the flat files are no longer updated, the data end around 2021). M-CSA: Ribeiro et
al., Nucleic Acids Research 46 (2018) D618, https://www.ebi.ac.uk/thornton-srv/m-csa/, licensed CC BY 4.0.

Output: gzipped CSV "pdb,site,chain,residue,number", one row per residue, sites numbered from 1 per PDB entry.
M-CSA groups all copies of a site in a structure (the reference site of an entry apart from its other copies); the
web places a site at the centre of its residues, so each copy should be a site of its own. Per M-CSA entry, structure
and reference flag, from the residue numbers of the chains:
- chains without a common residue number are parts of one site (1CQJ: A:Glu208 A:His246 B:Tyr109 B:Glu197): one site;
- chains with the same residues are copies: one site per chain; except for a reference site made of such chains
  (HIV protease, 1A30: Asp25 of both chains), whose structures give one site of up to that many chains;
- other chains (e.g. four copies of a two-chain site) cannot be paired without coordinates: one site per chain;
- in a chain, the one-chain sites that share a residue are merged: several M-CSA entries often describe one site (1A0J).
Checked with coordinates on the inter-chain sites of CSA 2.0 that this splits (173): 119 span over 25 A (copies or
different sites merged, e.g. 1AY4, 1A50), 25 are compact (e.g. 1AHJ, four copies of a two-chain site).
Residues: the author's chain and residue number, as the structure files have them. Records without them are skipped.
Compared with the former CSA 2.0 (2013, 25,358 entries): 32,643 entries more; 1,438 of its entries are not in M-CSA
(1,312 of them still current in the PDB), they have no active sites now.
The output does not depend on the order of the input, and the gzip file has no timestamp: the same input gives the
same file.
"""

import collections
import csv
import gzip
import io
import json
import sys

# Residues M-CSA names differently in different entries, checked in the structures (RCSB, 2026-10-17); None: not in
# the structure (1GKM has no chain A), the residue is left out. The data are no longer updated, other conflicts fail.
NAMES = {
    ("1nb3", "C", 25): "Cys", ("1nb5", "C", 25): "Cys",
    ("1b8f", "A", 280): "Tyr", ("1b8f", "A", 329): "Phe",
    ("1gkm", "A", 280): None, ("1gkm", "A", 329): None,
}

source, target = sys.argv[1], sys.argv[2]

groups = collections.defaultdict(lambda: collections.defaultdict(set))  # (pdb, M-CSA entry, reference) -> chain -> numbers
names = collections.defaultdict(set)  # (pdb, chain, number) -> residue names
reference = collections.defaultdict(lambda: collections.defaultdict(set))  # M-CSA entry -> chain -> numbers of its reference site
for record in json.load(open(source)):
    for r in record["residue_chains"]:
        if r["auth_resid"] is None or not r["code"] or not r["chain_name"]:
            continue
        pdb = r["pdb_id"].lower()
        groups[(pdb, record["mcsa_id"], r["is_reference"])][r["chain_name"]].add(r["auth_resid"])
        names[(pdb, r["chain_name"], r["auth_resid"])].add(r["code"])
        if r["is_reference"]:
            reference[record["mcsa_id"]][r["chain_name"]].add(r["auth_resid"])
# reference sites made of chains with the same residues (HIV protease): entry -> number of chains
identical_chains = {e: len(c) for e, c in reference.items() if len(c) > 1 and len({frozenset(n) for n in c.values()}) == 1}


def name(residue):
    if residue in NAMES:
        return NAMES[residue]
    found = names[residue]
    if len(found) > 1:
        sys.exit(f"{residue} is named {sorted(found)} in M-CSA, check the structure and add it to NAMES")
    return next(iter(found))


sites = collections.defaultdict(set)  # pdb -> sites, each a frozenset of (chain, number)
one_chain = collections.defaultdict(list)  # (pdb, chain) -> residue numbers, one set per M-CSA entry
for (pdb, entry, _), chains in groups.items():
    numbers = list(chains.values())
    parts = len(numbers) > 1 and all(not a & b for i, a in enumerate(numbers) for b in numbers[i + 1:])
    one_copy = len({frozenset(n) for n in numbers}) == 1 and 2 <= len(numbers) <= identical_chains.get(entry, 0)
    if parts or one_copy:
        sites[pdb].add(frozenset((c, n) for c, ns in chains.items() for n in ns))
    else:
        for chain, numbers in chains.items():
            one_chain[(pdb, chain)].append(set(numbers))
for (pdb, chain), sets in one_chain.items():
    merged: list[set] = []
    for s in sets:
        s = set(s)
        for m in [m for m in merged if m & s]:
            s |= m
            merged.remove(m)
        merged.append(s)
    sites[pdb].update(frozenset((chain, n) for n in m) for m in merged)

rows = []
for pdb in sorted(sites):
    site = 0
    for members in sorted(sorted(s) for s in sites[pdb]):
        residues = [(chain, number, name((pdb, chain, number))) for chain, number in members]
        residues = [r for r in residues if r[2] is not None]
        if residues:
            site += 1
            rows.extend((pdb, site, chain, residue, number) for chain, number, residue in residues)

text = io.StringIO()
writer = csv.writer(text, lineterminator="\n")
writer.writerow(["pdb", "site", "chain", "residue", "number"])
writer.writerows(rows)
with open(target, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0, filename="") as out:
    out.write(text.getvalue().encode())
print(f"{len({r[0] for r in rows})} PDB entries, {len({(r[0], r[1]) for r in rows})} sites, {len(rows)} residues", file=sys.stderr)
