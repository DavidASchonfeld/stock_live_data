# stock_live_data

#TODO: I need to rename this, since I'm pulling *weather* information.

**Kubernetes**
(I am using the K3S version, directly downloaded on my server)
Each pod contains/is running a docker image

-- Pod 1 + Nodeport:
---- Pod 1: Python Code + Flask to create the website
---- Nodeport Service: to expose "Pod 1" outside of the Kubernetes pod so people can access the website publicly

-- Pod 2 (Being Built Now): Extraction/Transfer: Pulling Information from API and pushing (aka producing) data to Kafka

-- Pod 3 (To Be Built) : Loading. Pulling data from Kafka and pushing data into SQL database


Hosted on an Amazon Linux 2023 instance