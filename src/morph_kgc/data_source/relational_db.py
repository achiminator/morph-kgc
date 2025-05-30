__author__ = "Julián Arenas-Guerrero"
__credits__ = ["Julián Arenas-Guerrero"]

__license__ = "Apache-2.0"
__maintainer__ = "Julián Arenas-Guerrero"
__email__ = "arenas.guerrero.julian@outlook.com"


import logging
import pandas as pd

from ..constants import *

LOGGER = logging.getLogger(LOGGING_NAMESPACE)

# PostgresSQL data types: https://www.postgresql.org/docs/14/datatype.html
# Oracle data types: https://docs.oracle.com/cd/A58617_01/server.804/a58241/ch5.htm
# MySQL data types: https://dev.mysql.com/doc/refman/8.0/en/data-types.html
SQL_RDF_DATATYPE = {
    'BINARY': XSD_HEX_BINARY,
    'VARBINARY': XSD_HEX_BINARY,
    'BLOB': XSD_HEX_BINARY,
    'BFILE': XSD_HEX_BINARY,
    'RAW': XSD_HEX_BINARY,
    'LONG RAW': XSD_HEX_BINARY,

    'INTEGER': XSD_INTEGER,
    'INT': XSD_INTEGER,
    'SMALLINT': XSD_INTEGER,
    'INT8': XSD_INTEGER,
    'INT4': XSD_INTEGER,
    'BIGINT': XSD_INTEGER,
    'BIGSERIAL': XSD_INTEGER,
    'SMALLSERIAL': XSD_INTEGER,
    'INT2': XSD_INTEGER,
    'SERIAL2': XSD_INTEGER,
    'SERIAL4': XSD_INTEGER,
    'SERIAL8': XSD_INTEGER,

    'DECIMAL': XSD_DECIMAL,
    'NUMERIC': XSD_DECIMAL,

    'FLOAT': XSD_DOUBLE,
    'FLOAT8': XSD_DOUBLE,
    'REAL': XSD_DOUBLE,
    'DOUBLE': XSD_DOUBLE,
    'DOUBLE PRECISION': XSD_DOUBLE,
    'NUMBER': XSD_DOUBLE,

    'BOOL': XSD_BOOLEAN,
    'TINYINT': XSD_BOOLEAN,
    'BOOLEAN': XSD_BOOLEAN,

    'DATE': XSD_DATE,
    'TIME': XSD_TIME,
    'DATETIME': XSD_DATETIME,
    'TIMESTAMP': XSD_DATETIME
}


def _replace_query_enclosing_characters(sql_query, db_dialect):
    dialect_sql_query = ''

    if db_dialect in [MYSQL, MARIADB]:
        dialect_sql_query = sql_query   # the query already uses backticks as enclosed characters
    elif db_dialect == MSSQL:
        # replace backticks with square brackets
        square_brackets = ['[', ']']
        num_enclosing_char = 0
        for char in sql_query:
            if char == '`':
                dialect_sql_query = dialect_sql_query + square_brackets[num_enclosing_char % 2]
                num_enclosing_char += 1
            else:
                dialect_sql_query = dialect_sql_query + char
    elif db_dialect == DATABRICKS:
        # remove all backticks
        dialect_sql_query = sql_query.replace('`', '')
    else:
        # replace backticks with double quotes
        dialect_sql_query = sql_query.replace('`', '"')

    return dialect_sql_query


def _relational_db_connection(config, source_name):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    connect_args = eval(config.get_connect_args(source_name)) if config.has_connect_args(source_name) else {}

    db_connection = create_engine(config.get_db_url(source_name), connect_args=connect_args, poolclass=NullPool)
    db_dialect = db_connection.dialect.name.upper()

    return db_connection, db_dialect


def _get_column_table_datatype(config, source_name, table_name, column_name):
    db_connection, db_dialect = _relational_db_connection(config, source_name)

    if db_dialect == ORACLE:
        sql_query = f"SELECT t.data_type FROM all_tab_columns t " \
                    f"WHERE t.TABLE_NAME = '{table_name}' AND t.COLUMN_NAME='{column_name}'"
    elif db_dialect == SQLITE:
        sql_query = f"SELECT typeof('{column_name}') as data_type FROM '{table_name}' LIMIT 1"
    else:
        sql_query = f"SELECT `data_type` FROM `information_schema`.`columns` " \
                    f"WHERE `table_name`='{table_name}' AND `column_name`='{column_name}'"
        sql_query = _replace_query_enclosing_characters(sql_query, db_dialect)

    query_results_df = pd.read_sql_query(sql_query, con=db_connection)

    if 'data_type' in query_results_df.columns and len(query_results_df) == 1:
        data_type = query_results_df['data_type'][0]
    elif 'DATA_TYPE' in query_results_df.columns and len(query_results_df) == 1:
        data_type = query_results_df['DATA_TYPE'][0]
    else:
        return None

    data_type = data_type.upper()
    for k, v in SQL_RDF_DATATYPE.items():
        if k in data_type:
            return v
    return None


def get_rdb_reference_datatype(config, rml_rule, reference):
    inferred_data_type = ''

    if rml_rule['logical_source_type'] == RML_TABLE_NAME:
        inferred_data_type = _get_column_table_datatype(config, rml_rule['source_name'],
                                                        rml_rule['logical_source_value'], reference)
    elif rml_rule['logical_source_type'] == RML_QUERY:
        import sql_metadata

        # if mapping rule has a query, get the table names in the query
        table_names = sql_metadata.Parser(rml_rule['logical_source_value']).tables
        for table_name in table_names:
            # for each table in the query get the datatype of the object reference in that table if an
            # exception is thrown, then the reference is not a column in that table, and nothing is done
            try:
                inferred_data_type = _get_column_table_datatype(config, rml_rule['source_name'],
                                                                table_name, reference)
                if inferred_data_type:
                    # already found it, end looping
                    break
            except:
                pass

    return inferred_data_type


def _build_sql_query(rml_rule, references):
    """
    Build a query for MYSQL using backticks '`' as enclosing character. This character will later be replaced with the
    one corresponding one to the dialect that applies. It also takes care of schema-qualified names.
    """

    if rml_rule['logical_source_type'] == RML_QUERY:
        query = rml_rule['logical_source_value']
    elif rml_rule['logical_source_type'] == RML_TABLE_NAME and len(references) > 0:
        query = 'SELECT ' # + 'DISTINCT ' # TODO: is this more efficient?
        # replacements of `.` to deal with schema-qualified names (see issue #89)
        for reference in references:
            query = f"{query}`{reference.replace('.', '`.`')}`, "
        query = f"{query[:-2]} FROM `{rml_rule['logical_source_value'].replace('.', '`.`')}` WHERE "
        for reference in references:
            query = f"{query}`{reference.replace('.', '`.`')}` IS NOT NULL AND "
        query = query[:-5]
    else:
        query = None

    return query


def get_sql_data(config, rml_rule, references):
    sql_query = _build_sql_query(rml_rule, references)
    if sql_query is None:
        # in case all term maps are constants e.g. R2RML test case R2RMLTC0006a
        return pd.DataFrame(columns=list(references))

    db_connection, db_dialect = _relational_db_connection(config, rml_rule['source_name'])
    sql_query = _replace_query_enclosing_characters(sql_query, db_dialect)

    LOGGER.debug(f"SQL query for mapping rule `{rml_rule['triples_map_id']}`: [{sql_query}]")

    return pd.read_sql_query(sql_query, con=db_connection, coerce_float=False)
