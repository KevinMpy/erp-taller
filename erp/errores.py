class ErrorNegocio(Exception):
    """Regla de negocio incumplida (stock insuficiente, transición no válida...)."""

    def __init__(self, mensaje: str, detalle: object | None = None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.detalle = detalle


class NoEncontrado(ErrorNegocio):
    pass
