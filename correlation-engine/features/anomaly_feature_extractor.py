class AnomalyFeatureExtractor:

    def extract(self, anomaly_data=None):

        if not anomaly_data:

            return {}

        features = {}

        for service, anomaly in anomaly_data.items():

            features[service] = {

                "latency_zscore":

                    anomaly.get(
                        "latency_zscore",
                        0
                    ),

                "anomaly_score":

                    anomaly.get(
                        "anomaly_score",
                        0
                    ),

                "is_anomalous":

                    anomaly.get(
                        "anomaly_score",
                        0
                    ) > 0.8

            }

        return features