import pandas as pd

file = r"C:\Users\David.Olamijulo\downloads\parameter\data\2ISW_Parameter_File 5.xlsx"

xls = pd.ExcelFile(file)
print("Sheet names:", xls.sheet_names)
print()

for sheet in xls.sheet_names:
    print(f"=== Sheet: {sheet} ===")
    df = pd.read_excel(xls, sheet_name=sheet, nrows=0)
    print(f"Columns ({len(df.columns)}): {list(df.columns)}")
    print()

    # Read all data
    df = pd.read_excel(xls, sheet_name=sheet)
    print(f"Shape: {df.shape}")
    print(f"Dtypes:\n{df.dtypes}")
    print()
    print("First 10 rows:")
    print(df.head(10).to_string())
    print()

    # Summary stats for numeric columns
    num_cols = df.select_dtypes(include=['number']).columns
    if len(num_cols) > 0:
        print("Numeric summary:")
        print(df[num_cols].describe().to_string())
        print()

    # Check for missing values
    missing = df.isnull().sum()
    missing = missing[missing > 0]
    if len(missing) > 0:
        print("Columns with missing values:")
        print(missing.to_string())
        print()

    print("\n")
