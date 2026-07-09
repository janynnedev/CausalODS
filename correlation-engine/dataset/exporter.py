import csv



class DatasetExporter:



    def export(
        self,
        dataset,
        filename
    ):


        if not dataset:
            return


        with open(
            filename,
            "w",
            newline=""
        ) as file:


            writer = csv.DictWriter(

                file,

                fieldnames=
                dataset[0].keys()

            )


            writer.writeheader()


            writer.writerows(
                dataset
            )