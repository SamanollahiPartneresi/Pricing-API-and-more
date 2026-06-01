"""
Generates a single SQL statement that replaces the pricing_factors table with
the REAL production pricing factors transcribed from the SL_Heaven Service
Details Management UI (PCA Equity PLINK 353, ESA PLINK 301, PCA Debt PLINK 346).

Service ID assignments:
  1 = PCA Equity   (production values)
  2 = ESA          (production values)
  3 = Zoning       (placeholder demo; SL_Heaven has no real Zoning factors yet)
  4 = PCA Debt     (production values, NEW)

These supersede the older `*_PRICING_FACTORS_DEFAULTS` constants in
app/helpers/constants/pricing_factors_constants_helper.rb, which have drifted
~21% from production for PCA Equity and ~8% for ESA. The production UI is the
canonical source.

Output: prints SQL to stdout. Use the output for a Keboola SQL transformation
that targets in.c-Pricing_Agent_Input_Data.pricing_factors with full-load mode.
"""

# PCA EQUITY (PLINK 353) — production-current values
PCA_EQUITY: list[tuple[str, str, str, str]] = [
    ("Travel Difficulty", "1", "< 60 minute drive", "0"),
    ("Travel Difficulty", "2", "1-3 hour drive", "7"),
    ("Travel Difficulty", "3", "3-5 hour drive", "15"),
    ("Travel Difficulty", "4", "5+ hour drive", "20"),
    ("Travel Difficulty", "5", "Easy flight", "25"),
    ("Travel Difficulty", "6", "Hard flight", "RFP"),
    ("Travel Difficulty", "7", "Super remote", "RFP"),
    ("Prior Report", "1", "None", "0.00"),
    ("Prior Report", "2", "External < 2 years", "-2.50"),
    ("Prior Report", "3", "Internal < 10 years", "-5.00"),
    ("Prior Report", "4", "Internal < 2 years", "-10.00"),
    ("Prior Report", "5", "Internal < 6 months", "-15.00"),
    ("Site Complexity", "1", "Simple", "-5"),
    ("Site Complexity", "2", "Average", "0"),
    ("Site Complexity", "3", "Complicated", "20"),
    ("Site Complexity", "4", "Difficult", "RFP"),
    ("International", "1", "US", "0"),
    ("International", "2", "CA", "RFP"),
    ("Portfolio Size", "1", "1 to 3", "0"),
    ("Portfolio Size", "2", "4 to 9", "-2"),
    ("Portfolio Size", "3", "10 to 39", "-5"),
    ("Portfolio Size", "4", "40 to 69", "-7"),
    ("Portfolio Size", "5", "70 to 99", "-9"),
    ("Portfolio Size", "6", "100+", "-12"),
    ("Limit of Liability", "1", "Less than or equal to 249,999", "0"),
    ("Limit of Liability", "2", "Between 250,000 and 999,999; inclusive of 250,000", "2.5"),
    ("Limit of Liability", "3", "Between 1,000,000 and 4,999,999; inclusive of 1,000,000", "5"),
    ("Limit of Liability", "4", "5,000,000 or greater; inclusive of 5,000,000", "7.5"),
    ("Turnaround Time", "1", "1 day", "RFP"),
    ("Turnaround Time", "2", "2 days", "RFP"),
    ("Turnaround Time", "3", "3 days", "RFP"),
    ("Turnaround Time", "4", "4 days", "RFP"),
    ("Turnaround Time", "5", "5 days", "RFP"),
    ("Turnaround Time", "6", "6 days", "RFP"),
    ("Turnaround Time", "7", "7 days", "RFP"),
    ("Turnaround Time", "8", "8 days", "RFP"),
    ("Turnaround Time", "9", "9 days", "RFP"),
    ("Turnaround Time", "10", "10 days", "20"),
    ("Turnaround Time", "11", "11 days", "10"),
    ("Turnaround Time", "12", "12 days", "10"),
    ("Turnaround Time", "13", "13 days", "0"),
    ("Turnaround Time", "14", "14 days", "0"),
    ("Turnaround Time", "15", "15 days", "0"),
    ("Turnaround Time", "16", "16 days", "0"),
    ("Turnaround Time", "17", "17 days", "-5"),
    ("Turnaround Time", "18", "18 days", "-5"),
    ("Turnaround Time", "19", "19 days", "-5"),
    ("Turnaround Time", "20", "20 days", "-5"),
    ("Size", "1", "XS: Applied to certain Primary Property Types", "-10"),
    ("Size", "2", "S: Building SF <= 50,000", "0"),
    ("Size", "3", "M: Building SF <= 100,000", "5"),
    ("Size", "4", "L:Building SF <= 250,000", "7"),
    ("Size", "5", "XL:Building SF <= 500,000", "RFP"),
    ("Size", "6", "2XL:Building SF <= 1,000,000", "RFP"),
    ("Size", "7", "3XL: Building SF > 1,000,000", "RFP"),
    ("Unit Inspection", "1", "1 to 20 units", "0"),
    ("Unit Inspection", "2", "21 to 40 units", "15"),
    ("Unit Inspection", "3", "41 to 60 units", "25"),
    ("Unit Inspection", "4", "61 to 80 units", "RFP"),
    ("Unit Inspection", "5", "81 to 100 units", "RFP"),
    ("# of Buildings 1", "1", "1 to 8 bldgs", "0"),
    ("# of Buildings 1", "2", "9 to 14 bldgs", "10"),
    ("# of Buildings 1", "3", "15 to 20 bldgs", "RFP"),
    ("# of Buildings 1", "4", "20+ bldgs", "RFP"),
    ("# of Buildings 2", "1", "1", "0"),
    ("# of Buildings 2", "2", "2 bldgs", "5"),
    ("# of Buildings 2", "3", "3 to 5 bldgs", "10"),
    ("# of Buildings 2", "4", "6 to 9 bldgs", "RFP"),
    ("# of Buildings 2", "5", "10+ bldgs", "RFP"),
    ("# of Stories", "1", "0 to 10 stories", "0"),
    ("# of Stories", "2", "11 to 15 stories", "RFP"),
    ("# of Stories", "3", "16+ stories", "RFP"),
    ("Time Period", "1", "Adjust depending on Partner's busy level", "35"),
]

# ESA (PLINK 301) — production-current values
ESA: list[tuple[str, str, str, str]] = [
    ("Unit Inspection", "1", "1 to 20 units", "0"),
    ("Unit Inspection", "2", "21 to 40 units", "10"),
    ("Unit Inspection", "3", "41 to 60 units", "20"),
    ("Unit Inspection", "4", "61 to 80 units", "RFP"),
    ("Unit Inspection", "5", "81 to 100 units", "RFP"),
    ("Travel Difficulty", "1", "< 60 minute drive", "0"),
    ("Travel Difficulty", "2", "1-3 hour drive", "5"),
    ("Travel Difficulty", "3", "3-5 hour drive", "10"),
    ("Travel Difficulty", "4", "5+ hour drive", "20"),
    ("Travel Difficulty", "5", "Easy flight", "25"),
    ("Travel Difficulty", "6", "Hard flight", "RFP"),
    ("Travel Difficulty", "7", "Super remote", "RFP"),
    ("Prior Report", "1", "None", "0.00"),
    ("Prior Report", "2", "External < 2 years", "-2.50"),
    ("Prior Report", "3", "Internal < 10 years", "-5.00"),
    ("Prior Report", "4", "Internal < 2 years", "-10.00"),
    ("Prior Report", "5", "Internal < 6 months", "-15.00"),
    ("Site Complexity", "1", "Simple", "-5"),
    ("Site Complexity", "2", "Average", "0"),
    ("Site Complexity", "3", "Complicated", "10"),
    ("Site Complexity", "4", "Difficult", "RFP"),
    ("International", "1", "US", "0"),
    ("International", "2", "CA", "RFP"),
    ("Portfolio Size", "1", "1 to 3", "0"),
    ("Portfolio Size", "2", "4 to 9", "-2"),
    ("Portfolio Size", "3", "10 to 39", "-5"),
    ("Portfolio Size", "4", "40 to 69", "-7"),
    ("Portfolio Size", "5", "70 to 99", "-9"),
    ("Portfolio Size", "6", "100+", "-12"),
    ("Limit of Liability", "1", "Less than or equal to 249,999", "0"),
    ("Limit of Liability", "2", "Between 250,000 and 999,999; inclusive of 250,000", "2.5"),
    ("Limit of Liability", "3", "Between 1,000,000 and 4,999,999; inclusive of 1,000,000", "5"),
    ("Limit of Liability", "4", "5,000,000 or greater; inclusive of 5,000,000", "7.5"),
    ("Turnaround Time", "1", "1 day", "RFP"),
    ("Turnaround Time", "2", "2 days", "RFP"),
    ("Turnaround Time", "3", "3 days", "RFP"),
    ("Turnaround Time", "4", "4 days", "RFP"),
    ("Turnaround Time", "5", "5 days", "RFP"),
    ("Turnaround Time", "6", "6 days", "RFP"),
    ("Turnaround Time", "7", "7 days", "RFP"),
    ("Turnaround Time", "8", "8 days", "RFP"),
    ("Turnaround Time", "9", "9 days", "RFP"),
    ("Turnaround Time", "10", "10 days", "20"),
    ("Turnaround Time", "11", "11 days", "10"),
    ("Turnaround Time", "12", "12 days", "10"),
    ("Turnaround Time", "13", "13 days", "0"),
    ("Turnaround Time", "14", "14 days", "0"),
    ("Turnaround Time", "15", "15 days", "0"),
    ("Turnaround Time", "16", "16 days", "0"),
    ("Turnaround Time", "17", "17 days", "-5"),
    ("Turnaround Time", "18", "18 days", "-5"),
    ("Turnaround Time", "19", "19 days", "-5"),
    ("Turnaround Time", "20", "20 days", "-5"),
    ("Size", "1", "XS: Applied to certain Primary Property Types", "-10"),
    ("Size", "2", "S: Land Ac <= 4", "0"),
    ("Size", "3", "M: Land Ac <= 8", "5"),
    ("Size", "4", "L: Land Ac <= 12", "7"),
    ("Size", "5", "XL: Land Ac <= 20", "RFP"),
    ("Size", "6", "2XL: Land Ac <= 50", "RFP"),
    ("Size", "7", "3XL: Land Ac > 50", "RFP"),
    ("Time Period", "1", "Adjust depending on Partner's busy level", "10"),
    ("# of Stories", "1", "0 to 10 stories", "0"),
    ("# of Stories", "2", "11 to 15 stories", "4"),
    ("# of Stories", "3", "16+ stories", "RFP"),
    ("# of Buildings 1", "1", "1 to 5 bldgs", "0"),
    ("# of Buildings 1", "2", "6 to 18 bldgs", "7"),
    ("# of Buildings 1", "3", "19 to 35 bldgs", "15"),
    ("# of Buildings 1", "4", "36+ bldgs", "RFP"),
    ("# of Buildings 2", "1", "1", "0"),
    ("# of Buildings 2", "2", "2 bldgs", "2"),
    ("# of Buildings 2", "3", "3 to 5 bldgs", "4"),
    ("# of Buildings 2", "4", "6 to 9 bldgs", "6"),
    ("# of Buildings 2", "5", "10+ bldgs", "RFP"),
]

# PCA DEBT (PLINK 346) — production-current values
PCA_DEBT: list[tuple[str, str, str, str]] = [
    ("Travel Difficulty", "1", "< 60 minute drive", "0"),
    ("Travel Difficulty", "2", "1-3 hour drive", "7"),
    ("Travel Difficulty", "3", "3-5 hour drive", "15"),
    ("Travel Difficulty", "4", "5+ hour drive", "20"),
    ("Travel Difficulty", "5", "Easy flight", "25"),
    ("Travel Difficulty", "6", "Hard flight", "RFP"),
    ("Travel Difficulty", "7", "Super remote", "RFP"),
    ("Prior Report", "1", "None", "0.00"),
    ("Prior Report", "2", "External < 2 years", "-2.50"),
    ("Prior Report", "3", "Internal < 10 years", "-5.00"),
    ("Prior Report", "4", "Internal < 2 years", "-10.00"),
    ("Prior Report", "5", "Internal < 6 months", "-15.00"),
    ("Site Complexity", "1", "Simple", "-5"),
    ("Site Complexity", "2", "Average", "0"),
    ("Site Complexity", "3", "Complicated", "20"),
    ("Site Complexity", "4", "Difficult", "RFP"),
    ("International", "1", "US", "0"),
    ("International", "2", "CA", "RFP"),
    ("Portfolio Size", "1", "1 to 3", "0"),
    ("Portfolio Size", "2", "4 to 9", "-2"),
    ("Portfolio Size", "3", "10 to 39", "-5"),
    ("Portfolio Size", "4", "40 to 69", "-7"),
    ("Portfolio Size", "5", "70 to 99", "-9"),
    ("Portfolio Size", "6", "100+", "-12"),
    ("Limit of Liability", "1", "Less than or equal to 249,999", "0"),
    ("Limit of Liability", "2", "Between 250,000 and 999,999; inclusive of 250,000", "2.5"),
    ("Limit of Liability", "3", "Between 1,000,000 and 4,999,999; inclusive of 1,000,000", "5"),
    ("Limit of Liability", "4", "5,000,000 or greater; inclusive of 5,000,000", "7.5"),
    ("Turnaround Time", "1", "1 day", "RFP"),
    ("Turnaround Time", "2", "2 days", "RFP"),
    ("Turnaround Time", "3", "3 days", "RFP"),
    ("Turnaround Time", "4", "4 days", "RFP"),
    ("Turnaround Time", "5", "5 days", "RFP"),
    ("Turnaround Time", "6", "6 days", "RFP"),
    ("Turnaround Time", "7", "7 days", "RFP"),
    ("Turnaround Time", "8", "8 days", "RFP"),
    ("Turnaround Time", "9", "9 days", "RFP"),
    ("Turnaround Time", "10", "10 days", "20"),
    ("Turnaround Time", "11", "11 days", "10"),
    ("Turnaround Time", "12", "12 days", "10"),
    ("Turnaround Time", "13", "13 days", "0"),
    ("Turnaround Time", "14", "14 days", "0"),
    ("Turnaround Time", "15", "15 days", "0"),
    ("Turnaround Time", "16", "16 days", "0"),
    ("Turnaround Time", "17", "17 days", "-5"),
    ("Turnaround Time", "18", "18 days", "-5"),
    ("Turnaround Time", "19", "19 days", "-5"),
    ("Turnaround Time", "20", "20 days", "-5"),
    ("Size", "1", "XS: Applied to certain Primary Property Types", "-10"),
    ("Size", "2", "S: Building SF <= 50,000", "0"),
    ("Size", "3", "M: Building SF <= 100,000", "5"),
    ("Size", "4", "L:Building SF <= 250,000", "7"),
    ("Size", "5", "XL:Building SF <= 500,000", "RFP"),
    ("Size", "6", "2XL:Building SF <= 1,000,000", "RFP"),
    ("Size", "7", "3XL: Building SF > 1,000,000", "RFP"),
    ("Unit Inspection", "1", "1 to 20 units", "0"),
    ("Unit Inspection", "2", "21 to 40 units", "10"),
    ("Unit Inspection", "3", "41 to 60 units", "20"),
    ("Unit Inspection", "4", "61 to 80 units", "RFP"),
    ("Unit Inspection", "5", "81 to 100 units", "RFP"),
    ("# of Buildings 1", "1", "1 to 8 bldgs", "0"),
    ("# of Buildings 1", "2", "9 to 14 bldgs", "10"),
    ("# of Buildings 1", "3", "15 to 20 bldgs", "RFP"),
    ("# of Buildings 1", "4", "20+ bldgs", "RFP"),
    ("# of Buildings 2", "1", "1", "0"),
    ("# of Buildings 2", "2", "2 bldgs", "2"),
    ("# of Buildings 2", "3", "3 to 5 bldgs", "4"),
    ("# of Buildings 2", "4", "6 to 9 bldgs", "6"),
    ("# of Buildings 2", "5", "10+ bldgs", "RFP"),
    ("# of Stories", "1", "0 to 10 stories", "0"),
    ("# of Stories", "2", "11 to 15 stories", "4"),
    ("# of Stories", "3", "16+ stories", "RFP"),
    ("Time Period", "1", "Adjust depending on Partner's busy level", "15"),
]

# Zoning placeholder data — kept unchanged until real Zoning factors are defined.
ZONING: list[tuple[str, str, str, str]] = [
    ("Turnaround Time", "1", "Less than or equal to 5 days", "25"),
    ("Turnaround Time", "2", "Less than or equal to 10 days", "15"),
    ("Turnaround Time", "3", "Less than or equal to 15 days", "8"),
    ("Turnaround Time", "4", "Greater than or equal to 20 days", "0"),
    ("Size", "XS", "Building SF <= 10000", "0"),
    ("Size", "S", "Building SF <= 30000", "0"),
    ("Size", "M", "Building SF <= 100000", "2"),
    ("Size", "L", "Building SF <= 250000", "3"),
    ("Size", "XL", "Building SF > 250000", "5"),
    ("# of Stories", "1", "1 to 3", "0"),
    ("# of Stories", "2", "4 to 10", "0"),
    ("# of Stories", "3", "11+", "2"),
    ("# of Buildings", "1", "1 to 2", "0"),
    ("# of Buildings", "2", "3 to 5", "5"),
    ("# of Buildings", "3", "6+", "10"),
    ("Travel Difficulty", "1", "Easy", "0"),
    ("Travel Difficulty", "2", "Moderate", "10"),
    ("Travel Difficulty", "3", "Difficult", "20"),
    ("Travel Difficulty", "4", "Remote", "30"),
    ("Limit of Liability", "1", "Less than or equal to 250000", "5"),
    ("Limit of Liability", "2", "Between 250000 and 1000000", "10"),
    ("Limit of Liability", "3", "Between 1000000 and 5000000", "15"),
    ("Limit of Liability", "4", "5000000 or greater", "25"),
    ("Portfolio Size", "1", "1 to 5", "0"),
    ("Portfolio Size", "2", "6 to 10", "5"),
    ("Portfolio Size", "3", "11 to 20", "10"),
    ("Portfolio Size", "4", "21+", "15"),
    ("Site Complexity", "1", "Simple", "0"),
    ("Site Complexity", "2", "Average", "15"),
    ("Site Complexity", "3", "Complicated", "30"),
    ("Site Complexity", "4", "Difficult", "50"),
    ("International", "1", "US", "0"),
    ("International", "2", "CA", "20"),
    ("Prior Report", "1", "Internal < 6 months", "-20"),
    ("Prior Report", "2", "Internal < 2 years", "-10"),
    ("Prior Report", "3", "Internal < 10 years", "0"),
    ("Prior Report", "4", "External < 2 years", "5"),
    ("Time Period", "1", "Busy Level", "5"),
]


def sql_quote(s: str) -> str:
    """Escape a string for SQL single-quoted literal."""
    return "'" + s.replace("'", "''") + "'"


def build() -> str:
    select_lines: list[str] = []
    factor_id = 0
    is_first = True

    # Service ID assignments (1 = PCA Equity, 2 = ESA, 3 = Zoning, 4 = PCA Debt)
    for service_id, rows_in in (
        (1, PCA_EQUITY),
        (2, ESA),
        (3, ZONING),
        (4, PCA_DEBT),
    ):
        for category, level, description, value in rows_in:
            factor_id += 1
            if is_first:
                line = (
                    f"SELECT {factor_id} AS \"factor_id\", {sql_quote(category)} AS \"category\", "
                    f"{sql_quote(level)} AS \"level\", {sql_quote(description)} AS \"description\", "
                    f"{sql_quote(value)} AS \"value\", FALSE AS \"default_flag\", "
                    f"{service_id} AS \"order_form_service_id\""
                )
                is_first = False
            else:
                line = (
                    f"SELECT {factor_id}, {sql_quote(category)}, {sql_quote(level)}, "
                    f"{sql_quote(description)}, {sql_quote(value)}, FALSE, {service_id}"
                )
            select_lines.append(line)

    body = "\nUNION ALL\n".join(select_lines)
    return f"CREATE OR REPLACE TABLE \"pricing_factors\" AS\n{body}\n"


if __name__ == "__main__":
    print(build())
