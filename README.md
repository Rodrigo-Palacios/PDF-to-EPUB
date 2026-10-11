# PDF to EPUB

Convierte guías y material de estudio en PDF a libros EPUB para leerlos desde el iPhone, un lector electrónico o cualquier aplicación compatible.

## Convertir desde el iPhone

El flujo remoto usa Codex Cloud para ejecutar el conversor y GitHub para guardar los archivos. No hace falta tener una computadora a mano.

1. Abre en Codex la conversación o el entorno conectado a `Rodrigo-Palacios/PDF-to-EPUB`.
2. Adjunta el PDF desde el iPhone y pide, por ejemplo: **«Convierte este PDF a EPUB y guarda el original y el EPUB en el repositorio»**.
3. La skill `pdf-to-epub` procesa el archivo en el entorno Cloud, sin pedir una ruta de salida.
4. El original queda en `input/` y el EPUB en `output/`, con el mismo nombre base. El resultado se guarda en la rama del repositorio para que puedas descargarlo desde GitHub en el iPhone.

El entorno Cloud requiere conexión a internet y acceso al repositorio. Este proyecto no mantiene un servidor propio encendido permanentemente: Codex inicia el entorno cuando se solicita una conversión.

## Convertir desde una computadora

Instala Python 3 y prepara el entorno:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p input output
```

Copia uno o varios PDF a `input/` y ejecuta:

```bash
.venv/bin/python scripts/pdf_to_epub.py
```

El conversor busca PDF recursivamente. Guarda cada EPUB en `output/` y conserva las subcarpetas. También puedes convertir un archivo específico:

```bash
.venv/bin/python scripts/pdf_to_epub.py \
  --input input/guia.pdf \
  --output output/guia.epub
```

## Qué conserva

- La primera página del PDF como portada del EPUB.
- Los marcadores del PDF como una tabla de contenido navegable, con enlaces a los capítulos.
- Los enlaces detectables dentro de las páginas, incluidos los del índice original.
- Las tablas con bordes detectables como tablas EPUB con filas y columnas.
- El texto seleccionable, organizado por página.

Los PDF escaneados sin capa de texto necesitan OCR antes de convertirlos. Los PDF protegidos con contraseña no se procesan. La detección automática de tablas se basa en sus bordes visibles; tablas sin bordes pueden requerir ajustes. Las imágenes interiores no se incorporan actualmente; la portada sí se conserva.

## Estructura del proyecto

- `.agents/skills/pdf-to-epub/SKILL.md`: instrucciones para usar el conversor desde Codex.
- `scripts/pdf_to_epub.py`: conversor de PDF a EPUB.
- `input/`: PDF de entrada.
- `output/`: EPUB generado.
- `requirements.txt`: dependencia de extracción de texto.
