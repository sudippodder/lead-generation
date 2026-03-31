import streamlit as st
import sqlite3
import pandas as pd
import os

# --- Configuration ---
DATABASE_FILE = "auth_db.sqlite"

def get_table_names(conn):
    """Retrieves a list of all table names from the database."""
    cursor = conn.cursor()
    # Query the built-in sqlite_master table to find all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    # Fetch all table names and convert them to a simple list
    tables = [item[0] for item in cursor.fetchall()]
    return tables

def get_table_data(conn, table_name):
    """Retrieves all rows from a specified table and returns a Pandas DataFrame."""
    query = f"SELECT * FROM {table_name}"
    # Use Pandas to efficiently read the SQL query result into a DataFrame
    df = pd.read_sql_query(query, conn)
    return df

def main():
    st.title("SQLite Database Viewer (Streamlit) 📊")

    # Check if the database file exists
    if not os.path.exists(DATABASE_FILE):
        st.error(f"Database file **'{DATABASE_FILE}'** not found in the current directory.")
        st.warning("Please create a file named 'test_database.db' and populate it with some data.")
        return

    # --- Database Connection and Table Retrieval ---
    try:
        # Establish connection to the SQLite database
        conn = sqlite3.connect(DATABASE_FILE)

        # Get the list of all tables
        table_names = get_table_names(conn)

    except sqlite3.Error as e:
        st.error(f"Error connecting to database: {e}")
        return

    # --- Display Logic ---
    if not table_names:
        st.info(f"The database **'{DATABASE_FILE}'** is empty. No tables found.")
    else:
        st.header(f"Tables found in **{DATABASE_FILE}**:")

        # Display the list of tables
        st.code(", ".join(table_names))
        st.markdown("---")

        # Iterate through each table to display its data
        for table_name in table_names:
            st.subheader(f"Data from table: **{table_name}**")

            try:
                # Retrieve all data for the current table
                df = get_table_data(conn, table_name)

                if df.empty:
                    st.write(f"Table **{table_name}** is empty.")
                else:
                    # Display the DataFrame as an interactive table in Streamlit
                    st.dataframe(df)

            except pd.io.sql.DatabaseError as e:
                st.warning(f"Could not read data from table **{table_name}**: {e}")

    # Close the database connection when done
    if conn:
        conn.close()

if __name__ == '__main__':
    main()
