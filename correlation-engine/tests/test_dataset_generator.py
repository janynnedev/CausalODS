from dataset.dataset_builder import DatasetBuilder
from dataset.exporter import DatasetExporter



builder = DatasetBuilder()


dataset = builder.generate(
    100
)



print(
    dataset[0]
)



exporter = DatasetExporter()


exporter.export(

    dataset,

    "results_rca_dataset.csv"

)