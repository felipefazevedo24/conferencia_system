# Obra Tracker

Aplicativo Flutter para **gestão de obras**, com acompanhamento de tarefas, cronograma de limpeza, cadastro de colaboradores e geração de relatórios em PDF.

## ✨ Funcionalidades

- Cadastro, edição e exclusão de obras
- Acompanhamento do progresso por tarefa
- Planejamento com status: não planejado, planejado, em andamento, pausado e concluído
- Registro de observações, impedimentos e fotos
- Cadastro de colaboradores
- Cronograma de limpeza por data e responsável
- Geração e compartilhamento de relatório em PDF
- Persistência local com SQLite

## 🧱 Estrutura

```text
lib/src/
  core/         serviços e utilitários
  data/         banco local e repositórios
  domain/       entidades do negócio
  presentation/ telas e providers
```

## ▶️ Como executar

1. Instale o Flutter SDK.
2. No diretório do projeto, execute sempre na mesma origem local para preservar os dados do navegador.

```bash
flutter pub get
flutter run -d chrome --web-hostname localhost --web-port 8080
```

No VS Code, você também pode usar a task [Run Web (localhost:8080)](.vscode/tasks.json) para subir o app sempre com a URL correta.

## 🧪 Testes

```bash
flutter test
```

## 📦 Stack

- `Flutter`
- `Riverpod`
- `sqflite`
- `intl`
- `pdf`
- `share_plus`
- `image_picker`

## Observação

O banco é local e inicializado em `AppDatabase`, com suporte desktop/web via `sqflite_common_ffi`.
