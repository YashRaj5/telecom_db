# Databricks notebook source
# MAGIC %md
# MAGIC # Telecommunications Reliability Metrics
# MAGIC ### Telecommunications LTE Architecture
# MAGIC
# MAGIC The modern telecommunications network consists of the Base Station also known as the **eNodeB (Evolved Node B)** for 4G networks is the hardware that communicates directly with the **UE (User Enitity such as a Mobile Phone)**. The **MME (Mobility Management Entity)** manages the entire process from a cell phone making a connection to a network to a paging message being sent to the mobile phone.
# MAGIC <img src = "https://raw.githubusercontent.com/databricks-industry-solutions/telco-reliability/main/images/Telco_simple.png">
# MAGIC
# MAGIC ### Use Case Overview
# MAGIC
# MAGIC * Telecommunications services collect many different forms of data to observe overall network reliability as well as to predict how best to expand the network to reach more customers. Some typical types of data collected are:
# MAGIC   * **PCMD (Per Call Measurement Data)**: granular details of all network processes as MME (Mobility Management Entity) manages processes between the UE and the rest of the network
# MAGIC   * **CDR (Call Detail Records)**: high level data describing call and SMS activity with fields such as phone number origin, phone number target, status of call/sms, duration, etc.
# MAGIC This data can be collected and used in provide a full view of the health of each cell tower in the network as well as the network as a whole.
# MAGIC
# MAGIC
# MAGIC **Note:** for this demo we will be primarily focused on CDR data but will also have a small sample of what PCMD could look like.
# MAGIC
# MAGIC **Business Impact of Solution**
# MAGIC
# MAGIC * **Ease of Scaling:** with large amounts of data being generated by a telecommunications system, Databricks can provide the ability to scale so that the data can be reliably ingested and analyzed.
# MAGIC * **Greater Network Reliability:** with the ability to monitor and predict dropped communications and more generally network faults, telecommunications providers can ultimately deliver better service for their customers and reduce churn.
# MAGIC
# MAGIC **Full Architecture from Ingestion to Analytics and Machine Learning**
# MAGIC
# MAGIC <img src = "https://raw.githubusercontent.com/databricks-industry-solutions/telco-reliability/main/images/telco_pipeline_full.png">

# COMMAND ----------

# MAGIC %md
# MAGIC **Data Ingestion with Delta Live Tables**
# MAGIC To simplify the ingestion process and accelerate our developments, we'll leverage Delta Live Table (DLT).
# MAGIC
# MAGIC DLT let you declare your transformations and will handle the Data Engineering complexity for you:
# MAGIC
# MAGIC * Data quality tracking with expectations
# MAGIC * Continuous or scheduled ingestion, orchestrated as pipeline
# MAGIC * Build lineage and manage data dependencies
# MAGIC * Automating scaling and fault tolerance
# MAGIC
# MAGIC **Bronze Layer**
# MAGIC
# MAGIC * Ingestion here starts with loading CDR and PCMD data directly from S3 using Autoloader. Though in this example JSON files are loaded into S3 from where Autoloader will then ingest these files into the bronze layer, streams from Kafka, Kinesis, etc. are supported by simply changing the "format" option on the read operation.
# MAGIC
# MAGIC <img src = "https://raw.githubusercontent.com/databricks-industry-solutions/telco-reliability/main/images/telco_pipeline_bronze.png">

# COMMAND ----------

import dlt
 
@dlt.table(comment="CDR Stream - Bronze")
def cdr_stream_bronze():
  return (spark.readStream.format("cloudFiles")  
                          .option("cloudFiles.format", 'json') 
                          .option('header', 'false')  
                          .option("mergeSchema", "true")         
                          .option("cloudFiles.inferColumnTypes", "true") 
                          .load("s3a://db-gtm-industry-solutions/data/CME/telco/CDR"))

# COMMAND ----------

@dlt.table(comment="PCMD Stream - Bronze")
def pcmd_stream_bronze():
  return (spark.readStream.format("cloudFiles")  
                          .option("cloudFiles.format", 'json') 
                          .option('header', 'false')  
                          .option("mergeSchema", "true")         
                          .option("cloudFiles.inferColumnTypes", "true") 
                          .load("s3a://db-gtm-industry-solutions/data/CME/telco/PCMD"))

# COMMAND ----------

# MAGIC %md
# MAGIC # Joining with Tower Data and Creating the Silver Layer
# MAGIC ### Silver Layer
# MAGIC
# MAGIC * In the silver layer, the data is refined removing nulls and duplicates while also joining tower information such as state, longitude, and latitude to allow for geospatial analysis. Stream-static joins are performed to do this with the streaming CDR and PCMD records being joined with static tower information which has been stored previously.
# MAGIC
# MAGIC
# MAGIC <img src = "https://raw.githubusercontent.com/databricks-industry-solutions/telco-reliability/main/images/telco_pipeline_silver.png">

# COMMAND ----------

@dlt.view
def static_tower_data():
  df_towers = spark.read.json("s3a://db-gtm-industry-solutions/data/CME/telco/cell_towers.json.gz")
  
  return df_towers.select(
                  df_towers.properties.GlobalID.alias("GlobalID"), 
                  df_towers.properties.LocCity.alias("City"), 
                  df_towers.properties.LocCounty.alias("County"), df_towers.properties.LocState.alias("State"), 
                  df_towers.geometry.coordinates[0].alias("Longitude"), 
                  df_towers.geometry.coordinates[1].alias("Latitude")
                 )

# COMMAND ----------

import pyspark.sql.functions as F
 
@dlt.table(comment="CDR Stream - Silver (Tower Info Added)")
@dlt.expect_or_drop("towerId", "towerId IS NOT NULL")
@dlt.expect_or_drop("type", "type IS NOT NULL")
def cdr_stream_silver():
  #get static tower data
  df_towers = dlt.read("static_tower_data")
  
  df_cdr_bronze = dlt.read_stream("cdr_stream_bronze")
  #join CDR data with tower data
  return df_cdr_bronze.join(df_towers, df_cdr_bronze.towerId == df_towers.GlobalID)

# COMMAND ----------

@dlt.table(comment="PCMD Stream - Silver (Tower Info Added)")
@dlt.expect_or_drop("towerId", "towerId IS NOT NULL")
@dlt.expect_or_drop("ProcedureId", "ProcedureId IS NOT NULL")
@dlt.expect("ProcedureDuration", "ProcedureDuration > 0")
def pcmd_stream_silver():
  #get static tower data
  df_towers = dlt.read("static_tower_data")
  
  df_pcmd_bronze = dlt.read_stream("pcmd_stream_bronze")
  #join PCMD data with
  return df_pcmd_bronze.join(df_towers, df_pcmd_bronze.towerId == df_towers.GlobalID)

# COMMAND ----------

# MAGIC %md
# MAGIC # Aggregating on Various Time Periods to Create the Gold Layer
# MAGIC With **Spark Structured Streaming** the streaming records can be automatically aggregated with stateful processing. Here the aggregation is done on 1 minute intervals and the KPIs are aggregated accordingly. Any interval can be selected here and larger time window aggregations can be done on a scheduled basis with Databricks Workflows. For example, the records that are aggregated here at 1 minute intervals can then be aggregated to hour long intervals with a workflow that runs every hour.
# MAGIC
# MAGIC <img src = "https://raw.githubusercontent.com/databricks-industry-solutions/telco-reliability/main/images/telco_pipeline_gold.png">

# COMMAND ----------

import pyspark.sql.functions as F
 
@dlt.table(comment="Aggregate CDR Stream - Gold (by Minute)")
def cdr_stream_minute_gold():
  df_cdr_silver = dlt.read_stream("cdr_stream_silver")
  
  
  df_cdr_pivot_on_status_grouped_tower = (df_cdr_silver 
                                                 .groupBy(F.window("event_ts", "1 minute"), "towerId")                       
                                                 .agg(F.count(F.when(F.col("status") == "dropped", True)).alias("dropped"),   
                                                    F.count(F.when(F.col("status") == "answered", True)).alias("answered"),     
                                                    F.count(F.when(F.col("status") == "missed", True)).alias("missed"),         
                                                    F.count(F.when(F.col("type") == "text", True)).alias("text"),              
                                                    F.count(F.when(F.col("type") == "call", True)).alias("call"),              
                                                    F.count(F.lit(1)).alias("totalRecords_CDR"),                               
                                                    F.first("window.start").alias("window_start"),                              
                                                    F.first("Longitude").alias("Longitude"),                                    
                                                    F.first("Latitude").alias("Latitude"),                                      
                                                    F.first("City").alias("City"),                                              
                                                    F.first("County").alias("County"),                                          
                                                    F.first("State").alias("state")
                                                   )                                            
                                                  .withColumn("date", F.col("window_start")))                              
  
 
 
  
  df_cdr_pivot_on_status_grouped_tower_ordered = (df_cdr_pivot_on_status_grouped_tower
                                                                            .select("date",              
                                                                                    "towerId",           
                                                                                    "answered",          
                                                                                    "dropped",           
                                                                                    "missed",            
                                                                                    "call",              
                                                                                    "text",              
                                                                                    "totalRecords_CDR",  
                                                                                    "Latitude",          
                                                                                    "Longitude",         
                                                                                    "City",              
                                                                                    "County",            
                                                                                    "State"
                                                                                   ))
  
  return df_cdr_pivot_on_status_grouped_tower_ordered

# COMMAND ----------

import pyspark.sql.functions as F
 
@dlt.table(comment="Aggregate PCMD Stream - Gold (by Minute)")
def pcmd_stream_minute_gold():
  df_pcmd_silver = dlt.read_stream("pcmd_stream_silver")
  
  df_pcmd_pivot_on_status_grouped_tower = (df_pcmd_silver 
                                                  .groupBy(F.window("event_ts", "1 minute"), "towerId")                                                                     
                                                  .agg(F.avg(F.when(F.col("ProcedureId") == "11", F.col("ProcedureDuration"))).alias("avg_dur_request_to_release_bearer"),  
                                                    F.avg(F.when(F.col("ProcedureId") == "15", F.col("ProcedureDuration"))).alias("avg_dur_notification_data_sent_to_UE"),    
                                                    F.avg(F.when(F.col("ProcedureId") == "16", F.col("ProcedureDuration"))).alias("avg_dur_request_to_setup_bearer"),         
                                                    F.max(F.when(F.col("ProcedureId") == "11", F.col("ProcedureDuration"))).alias("max_dur_request_to_release_bearer"),       
                                                    F.max(F.when(F.col("ProcedureId") == "15", F.col("ProcedureDuration"))).alias("max_dur_notification_data_sent_to_UE"),    
                                                    F.max(F.when(F.col("ProcedureId") == "16", F.col("ProcedureDuration"))).alias("max_dur_request_to_setup_bearer"),         
                                                    F.min(F.when(F.col("ProcedureId") == "11", F.col("ProcedureDuration"))).alias("min_dur_request_to_release_bearer"),       
                                                    F.min(F.when(F.col("ProcedureId") == "15", F.col("ProcedureDuration"))).alias("min_dur_notification_data_sent_to_UE"),    
                                                    F.min(F.when(F.col("ProcedureId") == "16", F.col("ProcedureDuration"))).alias("min_dur_request_to_setup_bearer"),         
                                                    F.count(F.lit(1)).alias("totalRecords_PCMD"),                                                                             
                                                    F.first("window.start").alias("window_start"),                                                                            
                                                    F.first("Longitude").alias("Longitude"),                                                                                  
                                                    F.first("Latitude").alias("Latitude"),                                                                                   
                                                    F.first("City").alias("City"),                                                                                         
                                                    F.first("County").alias("County"),             
                                                    F.first("State").alias("state")
                                                   )              
                                                  .withColumn("date", F.col("window_start")))
  
  df_pcmd_pivot_on_status_grouped_tower_ordered = (df_pcmd_pivot_on_status_grouped_tower
                                                                                .select("date",  
                                                                                        "towerId", 
                                                                                        "avg_dur_request_to_release_bearer", 
                                                                                        "avg_dur_notification_data_sent_to_UE", 
                                                                                        "avg_dur_request_to_setup_bearer", 
                                                                                        "max_dur_request_to_release_bearer", 
                                                                                        "max_dur_notification_data_sent_to_UE", 
                                                                                        "max_dur_request_to_setup_bearer", 
                                                                                        "min_dur_request_to_release_bearer", 
                                                                                        "min_dur_notification_data_sent_to_UE", 
                                                                                        "min_dur_request_to_setup_bearer", 
                                                                                        "totalRecords_PCMD", 
                                                                                        "Latitude", 
                                                                                        "Longitude",
                                                                                        "City",     
                                                                                        "County",   
                                                                                        "State"
                                                                                      ))
  
  return df_pcmd_pivot_on_status_grouped_tower_ordered

# COMMAND ----------

# MAGIC %md
# MAGIC # Aggregating on Larger Time Windows Through Scheduled Batch Workflows
# MAGIC As a last step in this data pipeline, hourly and daily aggregations of tower KPIs will be created as seen in the steps below. This process has been included in this Delta Live Tables pipeline for illustrative purposes but would typically be run on a batch hourly or daily basis in a real world scenario.
# MAGIC
# MAGIC <img src = "https://raw.githubusercontent.com/databricks-industry-solutions/telco-reliability/main/images/telco_pipeline_batch.png">

# COMMAND ----------

import pyspark.sql.functions as F
 
@dlt.table(comment="Aggregate CDR Stream - Gold (by Hour)")
def cdr_stream_hour_gold():
  df_cdr_minute_gold = dlt.read_stream("cdr_stream_minute_gold")
 
  df_windowed_by_hour = (df_cdr_minute_gold
                          .groupBy(F.window("date", "1 hour"), "towerId")
                          .agg(F.sum(F.col("dropped")).alias("dropped"),   
                          F.sum(F.col("answered")).alias("answered"), 
                          F.sum(F.col("missed")).alias("missed"),
                          F.sum(F.col("text")).alias("text"),
                          F.sum(F.col("call")).alias("call"),
                          F.sum(F.col("totalRecords_CDR")).alias("totalRecords_CDR"),
                          F.first("City").alias("City"),
                          F.first("County").alias("County"),
                          F.first("State").alias("State"),
                          F.first("Latitude").alias("Latitude"),
                          F.first("Longitude").alias("Longitude"),                            
                          F.first("window.start").alias("datetime")))
 
  return df_windowed_by_hour

# COMMAND ----------

import pyspark.sql.functions as F
 
@dlt.table(comment="Aggregate CDR Stream - Gold (by Day)")
def cdr_stream_day_gold():
  df_cdr_minute_gold = dlt.read_stream("cdr_stream_minute_gold")
 
  df_windowed_by_day = (df_cdr_minute_gold
                          .groupBy(F.window("date", "1 day"), "towerId")
                          .agg(F.sum(F.col("dropped")).alias("dropped"),   
                          F.sum(F.col("answered")).alias("answered"), 
                          F.sum(F.col("missed")).alias("missed"),
                          F.sum(F.col("text")).alias("text"),
                          F.sum(F.col("call")).alias("call"),
                          F.sum(F.col("totalRecords_CDR")).alias("totalRecords_CDR"),
                          F.first("City").alias("City"),
                          F.first("County").alias("County"),
                          F.first("State").alias("State"),
                          F.first("Latitude").alias("Latitude"),
                          F.first("Longitude").alias("Longitude"),                            
                          F.first("window.start").alias("datetime")))
 
  return df_windowed_by_day
