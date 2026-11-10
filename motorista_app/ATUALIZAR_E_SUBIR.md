# Atualizar e Subir o Motorista App (Checklist Rápido)

Este guia é para atualizar o app Flutter do motorista com segurança e gerar APK para instalação.

## 1) Atualizar código do repositório

No PowerShell, na raiz do projeto:

```powershell
git checkout main
git pull origin main
```

## 2) Entrar na pasta do app

```powershell
cd motorista_app
```

## 3) Atualizar versão do app (obrigatório para nova instalação)

Edite `pubspec.yaml` e aumente o build no campo `version`.

Exemplo:
- de: `1.0.0+1`
- para: `1.0.1+2`

Regra:
- `1.0.1` = versão visível para o usuário
- `+2` = build interno Android (sempre precisa subir)

## 4) Baixar dependências e validar

```powershell
flutter pub get
flutter analyze
```

Se o `flutter analyze` acusar erro, corrija antes de gerar APK.

## 5) Gerar APK

### Debug (rápido para teste interno)

```powershell
flutter build apk --debug
```

Saída:
- `build/app/outputs/flutter-apk/app-debug.apk`

### Release (para distribuição)

```powershell
flutter build apk --release
```

Saída:
- `build/app/outputs/flutter-apk/app-release.apk`

Observação: no estado atual do projeto, a build `release` está assinando com chave debug no arquivo `android/app/build.gradle.kts`. Isso serve para homologação/distribuição interna, mas para produção o ideal é configurar keystore própria.

## 6) Testar no celular antes de distribuir

Checklist mínimo:
1. Abrir viagem planejada e iniciar.
2. Confirmar que rastreamento fica ativo.
3. Marcar chegada e concluir parada.
4. Concluir viagem com KM final.
5. Testar offline/online para validar fila de envio.

## 6.1) Distribuir para os motoristas (atualização pelo app)

A partir da versão 1.1.0 o app se atualiza sozinho:

1. No Sync: **Central de Viagens → Veículos e motoristas → App do motorista → Publicar APK novo** (só administrador).
2. Escolha o `build/app/outputs/flutter-apk/app-release.apk`. O Sync lê a versão de dentro do APK e recusa se o build não for maior que o publicado.
3. Na próxima vez que o motorista abrir o app aparece "Nova versão": ele toca em **Atualizar** e depois em **Instalar** (o Android sempre pede esse toque).

Cuidados:
- **Gere o APK sempre neste mesmo PC.** O release é assinado com a chave de debug do PC (`~/.android/debug.keystore`). Se gerar em outro computador, o Android recusa a atualização e o motorista precisa desinstalar e instalar de novo.
- A 1.1.0 (a primeira com atualização automática) precisa ser instalada à mão uma última vez.

## 7) Subir código no GitHub (se você alterou arquivos)

```powershell
cd ..
git add motorista_app
git commit -m "Motorista app: atualizacao e build"
git push origin main
```

## 8) Comandos prontos (sequência rápida)

```powershell
git checkout main
git pull origin main
cd motorista_app
flutter pub get
flutter analyze
flutter build apk --release
```

Se quiser, eu também gero um `.ps1` para automatizar esse fluxo com validações automáticas.
