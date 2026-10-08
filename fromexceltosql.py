import sqlite3
import openpyxl
import pandas as pd
import re
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--filename", type=str, help = "Name of Excel file.")
parser.add_argument("--sheetname", type=str, help = "Name of Excel sheet.")
parser.add_argument("--ext", type=str, default='xlsx', help = "Name of extension (no dot).")
args = parser.parse_args()


excel_file = f"{args.filename.strip()}.{args.ext.strip()}"
sqlite_db = 'gallica_database.db'
table_name = 'gallica'

#read the excel file into a pandas DataFrame
df = pd.read_excel(excel_file, sheet_name=f'{args.sheetname.strip()}', engine='openpyxl')
df = df.replace(r'^\s*$', None, regex=True)

#helper function to map pandas data types to sqlite data types
def map_dtype(pandas_type):
    if "int" in str(pandas_type).lower():
        return "INTEGER"
    elif "float" in str(pandas_type).lower():
        return "REAL"
    elif "datetime" in str(pandas_type).lower():
        return "TEXT"
    else:
        return "TEXT"

#build the columns for sql, clean the column names
sql_columns = []
for col_name, dtype in df.dtypes.items():
    clean_col_name = (
        str(col_name).replace(" ", "_").replace("-","_").replace("(", "").replace(")", "").replace("%", "")
    )
    sql_type = map_dtype(dtype)
    sql_columns.append(f'"{clean_col_name}" {sql_type}')

columns_query_string = ", ".join(sql_columns)

#connect to sqlite database (creates file if it doesn't exists)
conn = sqlite3.connect(sqlite_db)
cursor = conn.cursor()

#drop table if it already exists
cursor.execute(f"DROP TABLE IF EXISTS {table_name}")

#0 = FALSE, 1 = TRUE
create_table_query = f"""
    CREATE TABLE {table_name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        {columns_query_string},
        processed NOT NULL DEFAULT 0 CHECK (processed IN (0, 1))
    )
"""
cursor.execute(create_table_query)
conn.commit()

#ensure the dataframe headers match the clean sql columns exactly
df.columns = [str(col).replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "").replace("%", "") for col in df.columns]

#write data to sqlite table
df.to_sql(table_name, conn, if_exists='append', index=False)

conn.close()
print("Done!")
