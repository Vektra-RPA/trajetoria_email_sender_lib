"""Só existe para levar o README.md para DENTRO do pacote instalado (igual à lib de logs).

Toda a configuração está no `pyproject.toml`. Aqui o `build_py` copia o README da raiz para
`trajetoria_email/README.md`, então ele aparece em `site-packages/trajetoria_email/` de quem
instala — ao lado do código, onde quem estiver integrando a lib o encontra.
"""
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

_ROOT = Path(__file__).resolve().parent


class BuildPyWithReadme(build_py):
    def run(self):
        super().run()
        target = Path(self.build_lib) / "trajetoria_email" / "README.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_ROOT / "README.md", target)


setup(cmdclass={"build_py": BuildPyWithReadme})
