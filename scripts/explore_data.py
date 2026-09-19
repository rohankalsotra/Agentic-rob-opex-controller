"""
explore_data.py -- look at the budget file the way you would in Excel, but with code.

This does NOT change the file. It only reads it and prints what it finds.
Run it with:   python scripts/explore_data.py
"""

from pathlib import Path
import pandas as pd

# Show wider tables in Terminal so columns don't wrap onto a second line.
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)

# --- 1. READ THE FILE ---------------------------------------------------------------
DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "sample_opex_2026.xlsx"

# pd.read_excel opens the sheet and loads it into a "DataFrame" -- pandas' word
# for a table. Think of it as one Excel sheet held in the computer's memory.
df = pd.read_excel(DATA_FILE)

# --- 2. WHAT DID WE GET? --------------------------------------------------------------
print("1) SIZE (rows, columns):", df.shape)

print("\n2) EACH COLUMN AND ITS TYPE:")
print(df.dtypes)              # 'object' = text, 'int64' = whole number,
                              # 'float64' = decimal number, 'datetime64' = date

print("\n3) FIRST 5 ROWS:")
print(df.head())

# --- 3. THE CHECKS YOU WOULD DO BY HAND -------------------------------------------------
print("\n4) BLANK CELLS in each column:")
print(df.isna().sum())        # isna() marks blanks; sum() counts them

print("\n5) THE ROW WITH A BLANK ACTUAL:")
print(df[df["Actual"].isna()])          # df[condition] keeps only the matching rows

print("\n6) EXACT DUPLICATE ROWS (extra copies):", df.duplicated().sum())
print(df[df.duplicated(keep=False)])    # keep=False shows BOTH copies, not just the extra

# --- 4. WHY DUPLICATES MATTER ---------------------------------------------------------------
budget_with_dupes = df["Budget"].sum()
budget_without = df.drop_duplicates()["Budget"].sum()
print("\n7) TOTAL BUDGET")
print(f"   with the duplicate row:    ${budget_with_dupes:,.0f}")
print(f"   after removing duplicates: ${budget_without:,.0f}")
print(f"   overstated by:             ${budget_with_dupes - budget_without:,.0f}")

# --- 5. A PIVOT TABLE, IN CODE ------------------------------------------------------------------
print("\n8) PIVOT: total budget by cost center (rows) and category (columns)")
pivot = df.pivot_table(
    index="Cost Center",       # what goes down the left side
    columns="Category",        # what goes across the top
    values="Budget",           # the number to add up
    aggfunc="sum",             # how to combine: add them up
)
print(pivot.to_string())
