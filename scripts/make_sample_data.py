"""
make_sample_data.py -- builds a FAKE ("synthetic") OPEX workbook for development.

Nothing in here is real. Every name, vendor, PO and number is invented.

The workbook has TWO sheets, mirroring how real OPEX data works:
  1. "Budget"        one row per cost center + category + month (the plan)
  2. "Transactions"  one row per PO line item (the actual spend as it was posted)

Actual spend for a cost center/category/month = the SUM of its transactions.
We plant a handful of known situations so we can later check that our code
handles each one correctly. The "answer key" prints at the end.

Run it from the project folder with:   python scripts/make_sample_data.py
It writes:                             data/sample_opex_2026.xlsx
"""

import random
from pathlib import Path

import pandas as pd

# --- SETTINGS -----------------------------------------------------------------
SEED = 42                      # same seed -> the exact same file every time
rng = random.Random(SEED)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = PROJECT_ROOT / "data" / "sample_opex_2026.xlsx"

MONTHS = pd.date_range("2026-01-01", "2026-09-01", freq="MS")   # Jan-Sep = Q1-Q3


def month(m):                  # helper: month(7) -> July 2026
    return pd.Timestamp(2026, m, 1)


# --- THE FAKE COMPANY -----------------------------------------------------------
COST_CENTERS = {               # id -> (name, size multiplier)
    "CC-100": ("Field Sales", 1.5),
    "CC-200": ("Marketing", 1.0),
    "CC-300": ("Customer Success", 0.8),
    "CC-400": ("Engineering Ops", 1.2),
    "CC-500": ("Finance & Admin", 0.5),
}

OWNERS = {                     # invented people who own POs in each team
    "CC-100": ["Priya Raman", "Daniel Okafor", "Megan Torres"],
    "CC-200": ["Lucas Bennett", "Aiko Tanaka", "Sofia Marin"],
    "CC-300": ["Hannah Weiss", "Marcus Reid", "Elena Petrova"],
    "CC-400": ["Tomas Alvarez", "Grace Nakamura", "Owen Fitzgerald"],
    "CC-500": ["Amara Diallo", "Ben Kowalski", "Rachel Levin"],
}

CATEGORY_BASE_BUDGET = {       # typical MONTHLY budget (USD) at multiplier 1.0
    "Travel": 20_000,
    "Software & Subscriptions": 15_000,
    "Contractors": 30_000,
    "Events & Marketing": 12_000,
    "Training": 5_000,
    "Facilities & Equipment": 8_000,
}

PROJECTS = {                   # category -> (project name, description, vendor)
    "Travel": [
        ("Customer Onsite Visits", "Airfare and lodging for customer-facing onsite visits.", "Skyline Travel Group"),
        ("Quarterly Planning Offsite", "Travel and lodging for the quarterly planning offsite.", "Skyline Travel Group"),
        ("Partner Summit Attendance", "Travel for partner summit attendance.", "Harborview Corporate Travel"),
    ],
    "Software & Subscriptions": [
        ("CRM Licence Renewal", "Annual renewal of CRM user licences.", "Lattice Software"),
        ("Analytics Platform Seats", "Seat-based subscription for the analytics platform.", "Brightpath Analytics"),
        ("Collaboration Tools Subscription", "Team collaboration and messaging tools.", "Lattice Software"),
    ],
    "Contractors": [
        ("Data Migration Support", "Contract specialists supporting the data migration.", "Bluepeak Consulting"),
        ("Process Documentation Project", "Contractors documenting operating procedures.", "Harbor Advisory"),
        ("Customer Onboarding Specialists", "Temporary specialists for customer onboarding.", "Bluepeak Consulting"),
    ],
    "Events & Marketing": [
        ("Regional Customer Event", "Venue, catering and materials for a regional customer event.", "Summit Events Co"),
        ("Webinar Series", "Production and promotion of a webinar series.", "Cedar Media Studio"),
        ("Trade Show Booth", "Booth, shipping and staffing for a trade show.", "Summit Events Co"),
    ],
    "Training": [
        ("Sales Enablement Workshops", "Workshops for the sales team.", "Cedar Learning"),
        ("Leadership Development Programme", "Leadership programme for new managers.", "Northfield Learning"),
        ("Certification Vouchers", "Exam vouchers for professional certifications.", "Cedar Learning"),
    ],
    "Facilities & Equipment": [
        ("Office Equipment Refresh", "Replacement monitors, docks and peripherals.", "Orion Supplies"),
        ("Lab Hardware Purchase", "Hardware for the test lab.", "Orion Supplies"),
        ("Workspace Upgrades", "Furniture and workspace improvements.", "Meridian Workspaces"),
    ],
}

# --- PLANTED SITUATIONS (our "answer key") ------------------------------------------
# Spend that is unusually high or low: multiply the normal amount by this factor.
PLANTED_FACTORS = {}
for m in (7, 8, 9):
    PLANTED_FACTORS[("CC-100", "Contractors", month(m))] = 1.45      # overspend Jul-Sep
PLANTED_FACTORS[("CC-200", "Travel", month(8))] = 1.95               # one-month spike
for m in range(4, 10):
    PLANTED_FACTORS[("CC-400", "Software & Subscriptions", month(m))] = 0.40   # underspend Apr-Sep

# A budgeted line with NO transactions at all in that month:
NO_TRANSACTIONS = {("CC-200", "Events & Marketing", month(9))}

# Months where we force 2 transaction lines (needed for some scenarios below):
FORCE_TWO_LINES = {
    ("CC-300", "Training", month(2)),                 # will get a true duplicate
    ("CC-500", "Facilities & Equipment", month(6)),   # will get a blank amount
    ("CC-100", "Travel", month(4)),                   # will get a no-ID look-alike
}
# A month where two DIFFERENT POs post the exact same amount (a legit look-alike):
EQUAL_SPLIT = {("CC-300", "Software & Subscriptions", month(7))}

# --- STEP 1: create the POs (projects) -----------------------------------------------------
po_counter = 450000
pos = {}                       # (cost center, category) -> list of 2 POs
for cc_id in COST_CENTERS:
    for category in CATEGORY_BASE_BUDGET:
        chosen = rng.sample(PROJECTS[category], 2)     # 2 different projects
        pos[(cc_id, category)] = []
        for i, (pname, pdesc, vendor) in enumerate(chosen):
            po_counter += 1
            pos[(cc_id, category)].append({
                "PO Number": f"PO-{po_counter}",
                "PO Owner": rng.choice(OWNERS[cc_id]),
                "Project Name": pname,
                "Project Description": pdesc,
                "Vendor": vendor,
                "start": 0 if i == 0 else rng.choice([0, 1, 2, 3]),   # month index it begins
            })

# --- STEP 2: the Budget sheet and the raw transactions ---------------------------------------
budget_rows, txn_rows = [], []
for cc_id, (cc_name, size) in COST_CENTERS.items():
    for category, base in CATEGORY_BASE_BUDGET.items():
        budget = round(base * size, -2)
        for idx, m in enumerate(MONTHS):
            budget_rows.append({"Cost Center ID": cc_id, "Cost Center": cc_name,
                                "Category": category, "Month": m, "Budget": budget})

            key = (cc_id, category, m)
            if key in NO_TRANSACTIONS:
                continue                                   # nothing posted this month

            # The month's total spend: within +/-6% of budget, times any planted factor
            total = round(budget * (1 + rng.uniform(-0.06, 0.06)) * PLANTED_FACTORS.get(key, 1.0))
            active = [p for p in pos[(cc_id, category)] if p["start"] <= idx]

            if key in EQUAL_SPLIT:                         # two POs, identical amounts
                assert len(active) == 2
                total += total % 2                         # make it even
                amounts, lines_pos = [total // 2, total // 2], active
            elif key in FORCE_TWO_LINES:                   # two lines (maybe same PO)
                a = round(total * rng.uniform(0.35, 0.65))
                amounts, lines_pos = [a, total - a], [rng.choice(active), rng.choice(active)]
            elif len(active) == 2 and rng.random() >= 0.35:    # split across both POs
                a = round(total * rng.uniform(0.35, 0.65))
                amounts, lines_pos = [a, total - a], active
            else:                                          # one PO takes the whole month
                amounts, lines_pos = [total], [rng.choice(active)]

            for amount, po in zip(amounts, lines_pos):
                txn_rows.append({
                    "Transaction ID": None,                # assigned in step 3
                    "PO Number": po["PO Number"], "PO Owner": po["PO Owner"],
                    "Cost Center ID": cc_id, "Cost Center": cc_name, "Category": category,
                    "Vendor": po["Vendor"], "Project Name": po["Project Name"],
                    "Project Description": po["Project Description"],
                    "Posting Date": m + pd.Timedelta(days=rng.randint(0, 27)),
                    "Amount": amount,
                })

budget_df = pd.DataFrame(budget_rows)
txn = pd.DataFrame(txn_rows).sort_values(["Posting Date", "Cost Center ID", "Category"],
                                         kind="stable").reset_index(drop=True)

# --- STEP 3: give every transaction its own unique ID -----------------------------------------
txn["Transaction ID"] = [f"TXN-{i:06d}" for i in range(1, len(txn) + 1)]
next_id = len(txn) + 1


def rows_for(cc, cat, m):
    """Find the transaction rows for one cost center + category + month."""
    return txn[(txn["Cost Center ID"] == cc) & (txn["Category"] == cat)
               & (txn["Posting Date"].dt.to_period("M") == m.to_period("M"))]


extra_rows = []

# --- STEP 4: plant the special situations -------------------------------------------------------
# (A) TRUE DUPLICATE: the very same record (same Transaction ID) loaded twice.
r = rows_for("CC-300", "Training", month(2)).iloc[0]
dup_txn_id, dup_po = r["Transaction ID"], r["PO Number"]
extra_rows.append(r.to_dict())

# (B) LEGIT PO EXTENSION: same PO number, NEW Transaction ID, new amount, and a
#     description that explains the extension. Looks like a duplicate but is NOT.
ext_po = next(p for p in pos[("CC-400", "Contractors")] if p["start"] == 0)
extra_rows.append({
    "Transaction ID": f"TXN-{next_id:06d}",
    "PO Number": ext_po["PO Number"], "PO Owner": ext_po["PO Owner"],
    "Cost Center ID": "CC-400", "Cost Center": "Engineering Ops", "Category": "Contractors",
    "Vendor": ext_po["Vendor"], "Project Name": ext_po["Project Name"],
    "Project Description": "Extension: additional scope approved after mid-project review.",
    "Posting Date": pd.Timestamp(2026, 5, 27), "Amount": 9000,
})
ext_txn_id = f"TXN-{next_id:06d}"
next_id += 1

# (C) NO-ID LOOK-ALIKE: a row with a BLANK Transaction ID that copies another row
#     (same PO, owner, project, description, amount). Code cannot be sure -> needs a human.
sub = rows_for("CC-100", "Travel", month(4))
r = sub.loc[sub["Amount"].idxmin()].to_dict()
noid_orig_id, noid_po = r["Transaction ID"], r["PO Number"]
r["Transaction ID"] = None
extra_rows.append(r)

# (D) BLANK AMOUNT: a transaction line with no amount entered.
blank_idx = rows_for("CC-500", "Facilities & Equipment", month(6)).index[0]
blank_txn_id = txn.loc[blank_idx, "Transaction ID"]
txn.loc[blank_idx, "Amount"] = None

txn = pd.concat([txn, pd.DataFrame(extra_rows)], ignore_index=True)
txn = txn.sort_values(["Posting Date", "Transaction ID"], kind="stable",
                      na_position="last").reset_index(drop=True)

# (E) SAME-AMOUNT LOOK-ALIKE is already in the data (see EQUAL_SPLIT above).
eq = rows_for("CC-300", "Software & Subscriptions", month(7))

# --- STEP 5: save as a tidy Excel workbook ---------------------------------------------------------
OUTPUT_FILE.parent.mkdir(exist_ok=True)
with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
    budget_df.to_excel(writer, sheet_name="Budget", index=False)
    txn.to_excel(writer, sheet_name="Transactions", index=False)

    # A cell's VALUE and its display FORMAT are separate things (just like in Excel),
    # so we set the display format for each column directly.
    def style(sheet_name, widths, formats):
        ws = writer.sheets[sheet_name]
        ws.freeze_panes = "A2"
        for col, width in widths.items():
            ws.column_dimensions[col].width = width
        for col, fmt in formats.items():
            for cell in ws[col][1:]:                # [1:] skips the header row
                cell.number_format = fmt

    style("Budget",
          {"A": 16, "B": 20, "C": 26, "D": 12, "E": 12},
          {"D": "mmm yyyy", "E": '"$"#,##0'})
    style("Transactions",
          {"A": 15, "B": 12, "C": 18, "D": 16, "E": 18, "F": 26,
           "G": 26, "H": 32, "I": 62, "J": 14, "K": 12},
          {"J": "yyyy-mm-dd", "K": '"$"#,##0'})

# --- STEP 6: print a summary so you can check your copy matches mine ---------------------------------------
print(f"Saved: {OUTPUT_FILE}\n")
print(f"Budget sheet rows:        {len(budget_df)}")
print(f"Transactions sheet rows:  {len(txn)}")
print(f"Total budget:             ${budget_df['Budget'].sum():,.0f}")
print(f"Total transaction amount: ${txn['Amount'].sum():,.0f}   (blank amounts are skipped)")
print("\nPlanted situations (the answer key):")
print("  1. CC-100 Contractors: about 45% over budget, Jul-Sep")
print("  2. CC-200 Travel: about 95% over budget, August only")
print("  3. CC-400 Software & Subscriptions: about 60% under budget, Apr-Sep")
print("  4. CC-200 Events & Marketing, Sep: budgeted, but NO transactions at all")
print(f"  5. TRUE DUPLICATE: {dup_txn_id} ({dup_po}) appears twice with the same Transaction ID")
print(f"  6. LEGIT PO EXTENSION: {ext_po['PO Number']} has a second line, {ext_txn_id}, "
      "with a new Transaction ID (not a duplicate)")
print(f"  7. NO-ID LOOK-ALIKE: a row with a blank Transaction ID copies {noid_orig_id} "
      f"({noid_po}); needs a human to decide")
print(f"  8. BLANK AMOUNT: {blank_txn_id} has no amount entered")
print(f"  9. SAME-AMOUNT LOOK-ALIKE: {', '.join(eq['Transaction ID'])} post the same "
      f"${eq['Amount'].iloc[0]:,.0f} on different POs (legit)")
print("\nSide effects to expect (these variances are CAUSED by the situations above):")
print("  - #7 leaves a possible double-count in CC-100 Travel, Apr (about +42% vs budget)")
print("  - #6 legitimately pushes CC-400 Contractors, May about 25% over budget")
print("  - #8 makes CC-500 Facilities, Jun look about 40% under budget (an amount is missing)")
