# Motorista Sync (app do motorista)

App Flutter **novo, separado e Android-only** para substituir a página web
(`/motorista/viagem/<vid>/<token>`) que hoje roda no navegador do celular do
motorista. O problema que resolve: navegadores Android/iOS matam abas em
segundo plano de forma agressiva, então o rastreamento de GPS parava sozinho,
o "app" (aba) fechava e o motorista perdia a viagem em andamento. Este app
roda o rastreamento dentro de um **Foreground Service Android real**, com
notificação persistente, exatamente como apps de entrega (iFood, Uber etc.).

**Não há nenhuma mudança no backend.** O app consome os mesmos endpoints
públicos (protegidos por token HMAC na própria URL) que a página web já usa
— ver `conferencia_app/routes/viagem_routes.py` (blueprint `motorista_bp`).

## Como o motorista faz o pareamento

Nada muda no processo de hoje: o despachante gera o link do painel em
`/motorista/<mid>/painel-link` (tela administrativa) e manda por WhatsApp. O
motorista só cola esse mesmo link na tela inicial do app (em vez de abrir no
navegador) — o app extrai o `motorista_id` e o token de dentro do link e
guarda localmente.

## Rodando/compilando

Esta máquina não tem Flutter/Android SDK instalados, então este código não
foi compilado nem testado aqui — só escrito. Na sua máquina com Flutter e
Android Studio instalados (mesmo ambiente já usado para o `obra_tracker` em
`facilities/Facility-master`):

```bash
cd motorista_app
flutter pub get      # resolve as versões atuais de cada pacote do pubspec.yaml
flutter analyze      # confira se não há erro — ver nota abaixo sobre flutter_foreground_task
flutter build apk --debug
```

Instale o APK gerado (`build/app/outputs/flutter-apk/app-debug.apk`) num
**aparelho Android físico** — emulador não reproduz de forma realista o
comportamento de GPS/segundo plano que estamos tentando validar.

### Ponto de atenção: `flutter_foreground_task`

A API desse pacote mudou entre versões majors. Escrevi
`lib/src/core/services/location_tracking_service.dart` seguindo o desenho
estável das versões recentes (classe `TaskHandler` + `onRepeatEvent`), mas
**não consegui validar contra a versão exata que o `flutter pub get` vai
resolver** (sem acesso a internet/pub.dev daqui). Se `flutter analyze`
apontar alguma divergência de assinatura nesse arquivo, ajuste conforme o
exemplo da versão instalada (`flutter pub deps` mostra a versão resolvida;
o pacote publica um app de exemplo completo no pub.dev com o mesmo padrão).

## O que testar no aparelho

1. Colar o link do painel → deve validar e mostrar as viagens.
2. Abrir uma viagem `Planejada` → informar KM inicial → iniciar. O Android
   vai pedir permissão de localização (primeiro "durante o uso", depois
   "permitir sempre" — aceite as duas). Deve aparecer uma notificação
   persistente "Rastreamento ativo".
3. **Minimizar o app por alguns minutos** (ou apagar a tela) e conferir que
   a notificação continua lá e que novos pontos continuam chegando no
   backend (confira em `ViagemPosicao` no banco, ou no painel administrativo
   de viagens/rastreamento).
4. Marcar chegada / concluir uma parada.
5. Concluir a viagem com KM final → a notificação de rastreamento deve sumir.
6. Testar sem internet por um tempo (modo avião) e depois reconectar: os
   pontos coletados offline devem ser enviados na sequência certa ao voltar
   a conexão (fila persistida em SQLite, `pending_pings`).

## Fora de escopo (por enquanto)

- **iOS**: precisa de Mac + Xcode para compilar/assinar, não disponível.
- **Distribuição**: build/assinatura de release e como instalar nos
  aparelhos dos motoristas (Play Store interna, ou APK direto) fica a seu
  critério.
- QR code para pareamento e atualização automática do app dentro do próprio
  app — pareamento hoje é só colar o link; novas versões do app precisam ser
  redistribuídas manualmente (mesmo processo da instalação inicial).
- Paridade visual pixel a pixel com o painel escuro da versão web — o app
  usa Material 3 simples e claro, focado em confiabilidade, não em réplica
  visual.
