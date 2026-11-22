# Disposabl Exact

Starter kit for building apps and agents with Exact and Convex.

## First deployment from a new repository

Create a repository from this template and enable it in GitHub Actions and Blacksmith. Use a Convex project with Static Hosting enabled. Configure the following GitHub Actions secrets before the push that starts your first deployment.

| Scope | Name | Value |
| --- | --- | --- |
| Repository | `CONVEX_DEPLOY_KEY` | Convex production deploy key |
| `android-production` environment | `EXACT_UPDATE_SEED` | Independent 32-byte, base64-encoded mobile update signing seed |
| `android-production` environment | `ANDROID_APK_SIGNING` | JSON containing the base64 PKCS12 `keystore` and its `password`, with key alias `android` |

Create `android-production` in **Settings → Environments** and restrict it to `main`. Both mobile platforms use this environment. Environment secrets are available for public repositories on GitHub Free. [GitHub environment availability](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)

Set the Convex key at repository scope. To provision the mobile signing secrets, run the following once from your clone with a repository administrator's GitHub CLI login. A GitHub-hosted runner generates the keys and encrypts them with the environment's public key. The command saves the encrypted values and keeps any existing keys.

```sh
python3 .github/actions/exact-setup/scripts/setup-signing.py
```

Push or merge to `main` to deploy. The first deployment starts web, Android and iOS in parallel. Open **Deploy app** for the signed Android APK, the iPhone IPA and an unlisted AppDrop install link for each phone. Anyone with the corresponding link can download the app. Complete signing on the iPhone with your own Apple ID through AppDrop/Tuft. Free provisioning needs periodic renewal on the phone, including while the app receives over-the-air updates.

## iPhone builds

Blacksmith macOS 27 with Xcode builds the first IPA and any replacement native binary. Blacksmith Ubuntu 26.04 handles web, Android, and compatible iOS updates. The shared iOS setup bundle contains Rust and Hermes tools. Xcode and the Apple SDK come from the hosted runner.

## Subsequent deployments

Web uploads to Convex Static Hosting on every deployment. Android and iOS use Exact's signed update store on AppDrop. Compatible app view, TypeScript and asset changes publish to installed apps. Exact checks on launch or foreground and activates downloaded content on a subsequent cold launch. A native version bump in `apps/apps/app.json`, SDK change, or incompatible permissions or capabilities requires a replacement native build. The workflow uses the production build receipt to initialize each stream. For a new phone, use the existing install link. Compatible deployments keep that link in the Actions summary. CI saves it in the Convex environment as `EXACT_INSTALL_ANDROID` or `EXACT_INSTALL_IOS` after a successful native build.

Only the canonical repository publishes tools. Merge a tools PR into `disposabl-dev/disposabl-exact:main` to publish the version selected in [publish.yml](.github/workflows/publish.yml). Once that release exists, update the deployment pins in a separate PR. Clones download the published bundles on their first deployment and cache them for later runs.

The one-minute target covers web deployment and compatible mobile updates, including queueing and transfers.

### Android App Bundles and Google Play

Append `--play` to the signing setup command to also create the `ANDROID_PLAY_UPLOAD_SIGNING` environment secret. Set the `ANDROID_PLAY_CONNECTED` Actions **variable** to `true` in `android-production` and bump the app version for the first Play build. The default Android build produces a release APK signed with the app’s standalone key. A Play-connected build also produces an AAB signed with the Play upload key. If the artifacts have incompatible native receipts, CI keeps the verified APK and fails the job before publishing the AAB. [Play App Signing](https://developer.android.com/studio/publish/app-signing)

### Run the app locally

Install dependencies in `packages/backend`, then run `bun run convex dev` there to link a development backend. In `apps/apps`, run `bun exact.mjs --help` and `bun install`. Set `CONVEX_ORIGIN` in `.env.local` to the development WebSocket URL, for example `wss://your-deployment.convex.cloud`. Start the app with `bun exact.mjs web`. CI derives this value from the production Convex deployment. It also supplies the Exact SDK pin, Bun version, build number, and update channel. No Apple, App Store Connect, TestFlight, Play Console, or AppDrop credentials belong in Actions for the default workflow.

## Monorepo layout

- `apps/apps` for the Exact app
- `packages/backend` for the Convex backend

## Planned features

1. Authentication with Google sign-in
2. Payments with Autumn.js on the web and in-app purchases on Android, macOS, and iOS
