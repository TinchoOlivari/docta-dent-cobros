ADMIN = "administrator"
OPERATOR = "operator"
PROFESSIONAL = "professional"

ETIQUETAS = {ADMIN: "Administrador", OPERATOR: "Operador", PROFESSIONAL: "Profesional"}
CHOICES = list(ETIQUETAS.items())
ROL_A_PANTALLA = {
    ADMIN: "usuarios:panel",
    OPERATOR: "usuarios:operador",
    PROFESSIONAL: "usuarios:profesional",
}
