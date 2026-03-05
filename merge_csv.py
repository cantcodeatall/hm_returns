import pandas as pd
import glob
import os
import sys

def merge_csv_files(input_path=None, output_file='ReportsTransactionAll.csv', current_value=None):
    # If no input path is provided, use the current script location
    if input_path is None:
        input_path = os.path.dirname(os.path.abspath(__file__))
    
    # Use glob to get all CSV files in the specified directory
    all_files = glob.glob(os.path.join(input_path, "*.csv"))
    
    # Create an empty list to store individual dataframes
    df_list = []
    
    # Read each CSV file and append to the list
    for file in all_files:
        df = pd.read_csv(file, parse_dates=['Date'], dayfirst=True)
        df_list.append(df)
    
    # Concatenate all dataframes in the list
    merged_df = pd.concat(df_list, ignore_index=True)

    # Ensure 'Date' column is in datetime format and drop rows where conversion failed
    merged_df['Date'] = pd.to_datetime(merged_df['Date'], errors='coerce')
    merged_df = merged_df.dropna(subset=['Date'])

    # Sort the merged dataframe by date
    merged_df = merged_df.sort_values('Date')

    # Get the max date from all transactions
    max_date = merged_df['Date'].max()

    # Create the additional row for current value
    current_value_row = pd.DataFrame({
        'Transaction ID': [''],
        'Date': [max_date],
        'Description': ['Current Value'],
        'Property ID': [''],
        'Related order ID': [''],
        'Net price': [''],
        'Gross price': [''],
        'Order': [''],
        'Cash change': [current_value if current_value is not None else ''],
        'Balance': [''],
        'Market description': [''],
    })

    # Insert the current value row after the header
    final_df = pd.concat([current_value_row, merged_df], ignore_index=True)

    # Select only the relevant columns for the final output
    final_df = final_df[['Transaction ID', 'Date', 'Description', 'Property ID', 
                          'Related order ID', 'Net price', 'Gross price', 
                          'Order', 'Cash change', 'Balance', 'Market description']]

    # Write the final dataframe to a new CSV file
    final_df.to_csv(output_file, index=False, date_format='%d-%m-%Y, %H:%M:%S')
    
    print(f"Merged CSV file created: {output_file}")

if __name__ == "__main__":
    # Get command line arguments
    args = sys.argv[1:]
    
    # Set default values
    input_path = None
    output_file = 'ReportsTransactionAll.csv'
    current_value = None
    
    # Parse command line arguments
    if len(args) >= 1:
        input_path = args[0]
    if len(args) >= 2:
        output_file = args[1]
    if len(args) >= 3:
        try:
            current_value = float(args[2])
        except ValueError:
            print("Invalid current value. Using blank value.")
    
    # Call the function with provided or default arguments
    merge_csv_files(input_path, output_file, current_value)