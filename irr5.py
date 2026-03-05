import pandas as pd
import numpy as np
from scipy.optimize import brentq
from datetime import datetime, timedelta
import sys
import os
import yfinance as yf

def calculate_irr(file_name='ReportsTransactionAll.csv', etf_ticker='VWRP.L'):
    # Read and preprocess the transaction data
    df = pd.read_csv(file_name)
    df['Timestamp'] = pd.to_datetime(df.iloc[:, 1], format='%d-%m-%Y, %H:%M:%S')
    df.iloc[:, 8] = df.iloc[:, 8].replace('-', np.nan).str.replace(',', '').astype(float)
    df.sort_values('Timestamp', inplace=True)
    
    # Filter for relevant transaction types
    df = df[df.iloc[:, 2].isin(['Deposit', 'Withdraw', 'Current Value'])]
    
    # Assign cash flows (negative for deposits/withdrawals, positive for current value)
    df.loc[df.iloc[:, 2] == 'Deposit', 'Cash Flow'] = -df.iloc[:, 8]
    df.loc[df.iloc[:, 2] == 'Withdraw', 'Cash Flow'] = -df.iloc[:, 8]
    df.loc[df.iloc[:, 2] == 'Current Value', 'Cash Flow'] = df.iloc[:, 8]
    
    # Calculate time fractions for IRR
    df['Time Fraction'] = (df['Timestamp'] - df['Timestamp'].min()) / timedelta(days=365)
    df['Actual Time'] = df['Timestamp'].min() + df['Time Fraction'].apply(lambda x: timedelta(days=x * 365))
    
    # Calculate net investment and weighted average time
    net_investment = -df.loc[df.iloc[:, 2].isin(['Deposit', 'Withdraw']), 'Cash Flow'].sum()
    weights = df.loc[df.iloc[:, 2].isin(['Deposit', 'Withdraw']), 'Cash Flow'].abs()
    time_differences = (df.loc[df.iloc[:, 2].isin(['Deposit', 'Withdraw']), 'Timestamp'] - df['Timestamp'].min()) / timedelta(days=365)
    weighted_average_time = np.average(time_differences, weights=weights)
    
    # Calculate time difference
    current_time = df.loc[df.iloc[:, 2] == 'Current Value', 'Timestamp'].max()
    weighted_average_time_datetime = df['Timestamp'].min() + timedelta(days=weighted_average_time * 365)
    time_difference = current_time - weighted_average_time_datetime
    
    # Calculate IRR
    def present_value(rate, cash_flows, times):
        return np.sum(cash_flows / (1 + rate) ** times)
    
    def irr(cash_flows, times):
        try:
            return brentq(present_value, -0.99, 5, args=(cash_flows, times))
        except ValueError as e:
            print(f"Error calculating IRR: {e}")
            return None
    
    irr_result = irr(df['Cash Flow'].values, df['Time Fraction'].values)
    
    # Calculate ETF performance
    etf_df = calculate_etf_performance(df, etf_ticker)
    
    return irr_result, net_investment, time_difference, df, etf_df

def calculate_etf_performance(df, etf_ticker):
    """
    Simulate ETF performance based on the same cash flows as the investment.
    """
    # Get ETF historical data
    start_date = df['Timestamp'].min()
    end_date = df['Timestamp'].max()
    
    # Download ETF data
    try:
        etf_data = yf.download(etf_ticker, start=start_date, end=end_date)
        
        # Reset index to make Date a column
        etf_data = etf_data.reset_index()
        
        # Ensure we have the right columns
        if 'Adj Close' in etf_data.columns:
            price_col = 'Adj Close'
        else:
            price_col = 'Close'
            
        # Simplify the dataframe to just the columns we need
        etf_data = etf_data[['Date', price_col]].copy()
        etf_data.columns = ['Date', 'Close']  # Standardize column names
        
        # Make sure Date is datetime
        etf_data['Date'] = pd.to_datetime(etf_data['Date'])
        
    except Exception as e:
        print(f"Error downloading ETF data: {e}")
        raise
    
    # Create a daily dataframe for ETF simulation
    date_range = pd.date_range(start=start_date.date(), end=end_date.date(), freq='D')
    etf_df = pd.DataFrame({'Date': date_range})
    etf_df['Date'] = pd.to_datetime(etf_df['Date'])
    
    # Merge ETF prices - make sure indexes are aligned
    etf_df = pd.merge(etf_df, etf_data, on='Date', how='left')
    
    # Forward fill missing prices (weekends/holidays)
    etf_df['Close'] = etf_df['Close'].ffill()
    
    # Create a map of transaction dates to cash flows
    # We'll use the same sign convention as in the original calculation
    cash_flow_map = {}
    for _, row in df.iterrows():
        if row.iloc[2] in ['Deposit', 'Withdraw']:  # Using iloc[2] for the Description column
            date = row['Timestamp'].date()
            if date in cash_flow_map:
                cash_flow_map[date] += row['Cash Flow']
            else:
                cash_flow_map[date] = row['Cash Flow']
    
    # Add cash flows to ETF dataframe
    etf_df['Cash Flow'] = 0.0
    for date, cash_flow in cash_flow_map.items():
        date_idx = etf_df[etf_df['Date'].dt.date == date].index
        if len(date_idx) > 0:
            etf_df.loc[date_idx[0], 'Cash Flow'] = cash_flow
    
    # Calculate ETF shares and value
    etf_df['Shares'] = 0.0
    etf_df['Cumulative Shares'] = 0.0
    
    # Initialize with first cash flow
    first_valid_idx = etf_df[etf_df['Cash Flow'] != 0].index.min()
    if pd.notna(first_valid_idx):
        # Note: Cash Flow is negative for deposits (buying shares)
        # So negative cash flow / positive price = negative shares (buying)
        etf_df.loc[first_valid_idx, 'Shares'] = etf_df.loc[first_valid_idx, 'Cash Flow'] / etf_df.loc[first_valid_idx, 'Close']
        etf_df.loc[first_valid_idx, 'Cumulative Shares'] = etf_df.loc[first_valid_idx, 'Shares']
    
    # Calculate shares and running total for each subsequent cash flow
    for i in range(first_valid_idx + 1 if pd.notna(first_valid_idx) else 0, len(etf_df)):
        if etf_df.loc[i, 'Cash Flow'] != 0 and etf_df.loc[i, 'Close'] > 0:
            # Calculate shares bought (negative) or sold (positive)
            etf_df.loc[i, 'Shares'] = etf_df.loc[i, 'Cash Flow'] / etf_df.loc[i, 'Close']
        else:
            etf_df.loc[i, 'Shares'] = 0
            
        etf_df.loc[i, 'Cumulative Shares'] = etf_df.loc[i-1, 'Cumulative Shares'] + etf_df.loc[i, 'Shares']
    
    # Calculate value of ETF holdings (always positive)
    etf_df['ETF Value'] = etf_df['Cumulative Shares'].abs() * etf_df['Close']
    
    # Calculate time fractions for IRR
    etf_df['Time Fraction'] = (etf_df['Date'] - etf_df['Date'].min()) / timedelta(days=365)
    
    # Get final ETF value
    final_etf_value = etf_df['ETF Value'].iloc[-1]
    etf_df['Final ETF Value'] = final_etf_value
    
    # Add timestamp column for compatibility
    etf_df['Timestamp'] = pd.to_datetime(etf_df['Date'])
    
    return etf_df

def calculate_etf_irr(df, etf_df):
    """
    Calculate IRR for the ETF investment simulation.
    """
    # Create a clean dataframe for IRR calculation
    etf_irr_df = pd.DataFrame()
    
    # Get all cash flow events (use iloc to reference by position)
    description_col = df.columns[2]  # Get the name of the third column (description/transaction type)
    cash_flow_df = df[df[description_col].isin(['Deposit', 'Withdraw'])].copy()
    
    # Create a row for each cash flow event
    etf_irr_df['Timestamp'] = cash_flow_df['Timestamp']
    
    # Keep the same sign convention for cash flows:
    # Negative for deposits/investments, positive for withdrawals
    etf_irr_df['Cash Flow'] = cash_flow_df['Cash Flow']
    
    etf_irr_df['Time Fraction'] = (etf_irr_df['Timestamp'] - df['Timestamp'].min()) / timedelta(days=365)
    
    # Add the final value as a positive cash flow at the end
    final_value_date = df.loc[df[description_col] == 'Current Value', 'Timestamp'].max()
    
    # IMPORTANT: Final ETF value should be positive for IRR calculation
    # Since all deposits have negative sign, the final value should be positive to represent the return
    final_value = etf_df['ETF Value'].iloc[-1]
    
    final_row = pd.DataFrame({
        'Timestamp': [final_value_date],
        'Cash Flow': [final_value],  # Positive value representing the final portfolio value
        'Time Fraction': [(final_value_date - df['Timestamp'].min()) / timedelta(days=365)]
    })
    
    # Combine with the final value
    etf_irr_df = pd.concat([etf_irr_df, final_row], ignore_index=True)
    
    # Sort by timestamp to ensure correct order
    etf_irr_df = etf_irr_df.sort_values('Timestamp')
    
    # Debug information
    # print("ETF IRR Calculation Data:")
    # print(etf_irr_df)
    
    # Check if we have valid data for IRR calculation
    if len(etf_irr_df) < 2:
        print("Not enough data points for ETF IRR calculation")
        return None
    
    # Verify that the IRR calculation will work
    cash_flows = etf_irr_df['Cash Flow'].values
    times = etf_irr_df['Time Fraction'].values
    
    # For IRR calculation to work, we need both positive and negative cash flows
    if not ((cash_flows > 0).any() and (cash_flows < 0).any()):
        print("ETF IRR calculation requires both positive and negative cash flows")
        print("Cash flows:", cash_flows)
        
        # If all cash flows are negative except the last one
        if np.all(cash_flows[:-1] < 0) and cash_flows[-1] <= 0:
            print("All cash flows including final value are negative, which is unusual")
            print("Adjusting final value to be positive")
            cash_flows[-1] = abs(cash_flows[-1])  # Make final value positive
        
        # Check again after potential adjustment
        if not ((cash_flows > 0).any() and (cash_flows < 0).any()):
            print("Still unable to calculate IRR after adjustments")
            return None
    
    # Also check if the sum of cash flows is close to zero
    if abs(np.sum(cash_flows)) < 1e-6:
        print("Sum of cash flows is too close to zero for IRR calculation")
        return None
    
    # IRR calculation function
    def present_value(rate, cash_flows, times):
        return np.sum(cash_flows / (1 + rate) ** times)

    def irr(cash_flows, times):
        try:
            # Try a wider range of possible IRR values
            return brentq(present_value, -0.99, 10, args=(cash_flows, times))
        except ValueError as e:
            print(f"Error calculating ETF IRR: {e}")
            # Debug information
            print("Cash flows:", cash_flows)
            print("Times:", times)
            print("Sum of cash flows:", np.sum(cash_flows))
            
            # Try with different bounds if the function doesn't change sign in the initial interval
            try:
                print("Trying with extended bounds...")
                return brentq(present_value, -0.99, 100, args=(cash_flows, times))
            except ValueError:
                print("Still failed with extended bounds")
                return None
    
    return irr(cash_flows, times)

def create_additional_dataframe(df):
    """
    Create an additional dataframe for time-weighted calculations.
    """
    date_range = pd.date_range(start=df['Timestamp'].min().date(), end=df['Timestamp'].max().date(), freq='D')
    df2 = pd.DataFrame({'Date': date_range})
    df2['Cash Flow'] = 0.0
    
    # Aggregate cash flows by date
    for _, row in df.iterrows():
        timestamp = row['Timestamp']
        cash_flow = row['Cash Flow']
        date = timestamp.date()
        nearest_date = df2.loc[df2['Date'].sub(pd.Timestamp(date)).abs().idxmin(), 'Date']
        df2.loc[df2['Date'] == nearest_date, 'Cash Flow'] = df2.loc[df2['Date'] == nearest_date, 'Cash Flow'] + float(cash_flow)
    
    # Calculate running totals and time factors
    df2['Net Investment'] = df2['Cash Flow'].cumsum()
    
    # Use the Description column for Current Value if available, otherwise use the third column
    description_col = 'Description' if 'Description' in df.columns else df.columns[2]
    current_time = df.loc[df[description_col] == 'Current Value', 'Timestamp'].max()
    
    df2['Time Held'] = (current_time - df2['Date']) / pd.Timedelta(days=365)
    df2['Product'] = df2['Cash Flow'] * df2['Time Held']
    
    return df2

def save_dataframe(df, file_prefix='IRR_calculations_dataframe'):
    """
    Save dataframe to CSV with incrementing file names.
    """
    file_count = 0
    while True:
        file_name = f'{file_prefix}_{file_count:02}.csv'
        if not os.path.exists(file_name):
            break
        file_count += 1
    df.to_csv(file_name, index=False)
    print(f"Dataframe saved as {file_name}")

def main():
    if len(sys.argv) > 1:
        file_name = sys.argv[1]
    else:
        file_name = 'ReportsTransactionAll.csv'
    
    if len(sys.argv) > 2:
        etf_ticker = sys.argv[2]
    else:
        etf_ticker = 'VWRP.L'
    
    try:
        irr_result, net_investment, time_difference, df, etf_df = calculate_irr(file_name, etf_ticker)
        
        # Display portfolio performance
        if irr_result is None:
            print("Cannot compute the internal rate of return (IRR)")
        else:
            irr_percentage = irr_result * 100
        
        # Use the Description column for Current Value if available, otherwise use the third column
        description_col = 'Description' if 'Description' in df.columns else df.columns[2]
        current_value = df.loc[df[description_col] == 'Current Value', 'Cash Flow'].values[0]
        
        # Calculate time-weighted metrics
        df2 = create_additional_dataframe(df)
        net_time_weighted_investment = -df2['Product'].sum()
        
        # Calculate P&L and return
        pnl = current_value - net_investment
        totalreturn = pnl / net_investment if net_investment != 0 else 0
        
        # Calculate ETF performance
        etf_irr_result = calculate_etf_irr(df, etf_df)
        if etf_irr_result is not None:
            etf_irr_percentage = etf_irr_result * 100
        else:
            etf_irr_percentage = None
        
        # Calculate ETF metrics
        etf_final_value = etf_df['ETF Value'].iloc[-1]
        
        # Use the same logic for calculating net investment as in the original portfolio
        etf_net_cash_flow = -df.loc[df[description_col].isin(['Deposit', 'Withdraw']), 'Cash Flow'].sum()
        
        etf_pnl = etf_final_value - etf_net_cash_flow
        etf_total_return = etf_pnl / etf_net_cash_flow if etf_net_cash_flow != 0 else 0
        
        # Display results 
        current_date = datetime.now().strftime('%Y-%m-%d')
        print(current_date)        
        print('===========')
        print(f'Net Investment: {net_investment:.2f}')
        print(f'P&L: {pnl:.2f}')
        print(f'Final Value: {current_value:.2f}')
        print(f'Total return: {totalreturn * 100:.2f}%')
        print(f'Internal rate of return (IRR): {irr_percentage:.2f}%')
        print('===========')
        print(f'Value-Weighted Average Time Held: {time_difference.days // 365} years, {(time_difference.days % 365) // 30} months, {(time_difference.days % 365) % 30} days')
        print(f'Net time weighted investment (equivalent investment held for 1 year): {net_time_weighted_investment:.2f}')
        print('===========')
        print(f'{etf_ticker} equivalent investment returns:')
        print(f'P&L: {etf_pnl:.2f}')
        print(f'Final value: {etf_final_value:.2f}')
        print(f'Total Return: {etf_total_return * 100:.2f}%')
        if etf_irr_percentage is not None:
            print(f'Internal rate of return (IRR): {etf_irr_percentage:.2f}%')
        else:
            print('Internal rate of return (IRR): Cannot be calculated.')
        print('  ')

        # Save results
        save_dataframe(df, file_prefix='IRR_calculations_dataframe')
        save_dataframe(df2, file_prefix='IRR_calculations_dataframe2')
        save_dataframe(etf_df, file_prefix='ETF_calculations_dataframe')
        
    except Exception as e:
        print(f"Error in calculation: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()