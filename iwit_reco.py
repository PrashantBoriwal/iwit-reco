"""
IWIT - Inter Warehouse Inventory Transfer Recommendation
=========================================================

INPUT  : IWIT_test.xlsx  (3 sheets: Shortfall, Excess, Priority)
OUTPUT : IWITreco.csv    (FSN, Source FC, Destination FC, Reco Qty)

Algorithm
---------
For every product (FSN) that has both excess and shortfall:
  For each destination FC that has a shortfall:
    Walk through source FCs in priority-lane order.
    Transfer as much as possible from each source until:
      - the shortfall is fully covered, OR
      - all sources for this FSN are exhausted.
    Deduct from a GLOBAL excess pool so no unit is allocated twice.
"""

import pandas as pd
import logging
logging.basicConfig(level=logging.INFO)
# ── 1. LOAD DATA ────────────────────────────────────────────────────────────────

FILE = "IWIT_test.xlsx"   # ← update path if the file is elsewhere

shortfall = pd.read_excel(FILE, sheet_name="Shortfall")   # destinations needing stock
excess    = pd.read_excel(FILE, sheet_name="Excess")       # sources with spare stock
priority  = pd.read_excel(FILE, sheet_name="Priority")     # zone-lane priority scores

logging.info("Data loaded")
print(f"  Shortfall rows : {len(shortfall)}")
print(f"  Excess rows    : {len(excess)}")
print(f"  Priority rows  : {len(priority)}")


# ── 2. BUILD CANDIDATE TRANSFER TABLE ───────────────────────────────────────────
#
# Merge shortfall × excess on FSN to get every possible (source FC, dest FC) pair.
# Then attach the priority score for each zone-lane combination.

merged = pd.merge(shortfall, excess, on="FSN", suffixes=("_dest", "_src"))

# Lane key = "DestinationZone SourceZone"  e.g. "North South"
merged["lane"] = merged["DestnationZone"] + " " + merged["SorceZone"]

# Join priority scores (left join keeps rows even if a lane has no priority defined)
merged = pd.merge(
    merged,
    priority[["D-S", "Priority"]],
    left_on="lane",
    right_on="D-S",
    how="left"
)

# Sort so within each FSN the best priority lanes come first
merged = merged.sort_values(["FSN", "Priority"]).reset_index(drop=True)

print(f"\nCandidate transfer rows after merge: {len(merged)}")


# ── 3. GLOBAL EXCESS POOL ───────────────────────────────────────────────────────
#
# Key insight: excess at a source FC must be tracked across ALL destinations.
# If D117 has 59 units, those 59 units are shared across every destination
# that wants to pull from D117 — not refreshed per destination.

# Dict: (FSN, source_FC) → remaining available quantity
excess_pool = {
    (row["FSN"], row["FC"]): row["Excess"]
    for _, row in excess.iterrows()
}


# ── 4. ALLOCATION LOOP ──────────────────────────────────────────────────────────

recommendations = []

# Outer loop: one FSN at a time
for fsn, fsn_group in merged.groupby("FSN"):

    # Inner loop: one destination FC at a time (within this FSN)
    # groupby preserves the Priority sort order we applied in step 2
    for dest_fc, dest_group in fsn_group.groupby("FC_dest", sort=False):

        # How much does this destination still need?
        sf_remaining = dest_group["Sf"].iloc[0]

        # Try each source in priority order
        for _, row in dest_group.sort_values("Priority").iterrows():

            if sf_remaining <= 0:
                break   # destination fully covered — move to next dest FC

            src_key       = (fsn, row["FC_src"])
            src_available = excess_pool.get(src_key, 0)

            if src_available <= 0:
                continue   # this source is exhausted — try next source

            # Transfer as much as possible without exceeding either limit
            transfer = min(src_available, sf_remaining)

            recommendations.append({
                "FSN"        : fsn,
                "Source"     : row["FC_src"],
                "Destination": dest_fc,
                "Reco"       : round(transfer, 6),
            })

            # Deduct from global pool and remaining shortfall
            excess_pool[src_key] -= transfer
            sf_remaining         -= transfer


# ── 5. BUILD & SAVE OUTPUT ──────────────────────────────────────────────────────

output = pd.DataFrame(recommendations)
output = output[output["Reco"] > 0].reset_index(drop=True)   # drop zero rows (safety net)

print(f"\nTransfer recommendations: {len(output)} rows")
print()
print(output.to_string(index=False))

OUT_PATH = "IWITreco.csv"
output.to_csv(OUT_PATH, index=False)
print(f"\nSaved → {OUT_PATH}")


# ── 6. SUMMARY REPORT ───────────────────────────────────────────────────────────

print("\n── Summary ──────────────────────────────────")
summary = (
    output.groupby("FSN")
          .agg(Total_Reco=("Reco", "sum"), Transfers=("Reco", "count"))
          .reset_index()
)
exc_totals = excess.groupby("FSN")["Excess"].sum().reset_index().rename(columns={"Excess": "Total_Excess"})
summary = pd.merge(summary, exc_totals, on="FSN")
summary["Excess_Used_%"] = (summary["Total_Reco"] / summary["Total_Excess"] * 100).round(1)
print(summary.to_string(index=False))
