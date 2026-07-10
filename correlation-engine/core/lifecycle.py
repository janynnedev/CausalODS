from config.logging import logger


class Lifecycle:

    @staticmethod
    def startup():

        logger.info(
            "Correlation Engine iniciado."
        )

    @staticmethod
    def shutdown():

        logger.info(
            "Correlation Engine encerrado."
        )