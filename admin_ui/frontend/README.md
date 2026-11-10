# Admin UI Frontend

React-based administration interface for Asterisk AI Voice Agent.

## Tech Stack

- **React 18** - UI framework
- **TypeScript** - Type safety
- **Vite** - Build tool
- **Tailwind CSS** - Styling
- **React Router** - Client-side routing
- **Axios** - HTTP client

## Getting Started

### Prerequisites

- Node.js 22 (22.12.0 or newer within 22.x)
- npm 9+

### Installation

The commands below use nvm to select the version in `.nvmrc`. If Node 22.12+
within 22.x is already installed another way, skip the two `nvm` commands.

```bash
cd admin_ui/frontend
nvm install
nvm use
npm ci
```

### Development

```bash
npm run dev
# Opens at http://localhost:5173
```

The development server proxies API requests to the backend at `http://localhost:3003`.

### Build

```bash
npm run build
# Output: dist/
```

## Available Scripts

| Script | Description |
|--------|-------------|
| `npm run dev` | Start development server with HMR |
| `npm run build` | Build for production |
| `npm run preview` | Preview production build locally |
| `npm run lint` | Run ESLint on source files |
| `npm run lint:fix` | Auto-fix ESLint issues |
| `npm run format` | Format code with Prettier |
| `npm run format:check` | Check code formatting |
| `npm run audit` | Check for security vulnerabilities |

## Project Structure

```text
src/
├── auth/           # Authentication context and guards
├── components/     # Reusable UI components
│   ├── config/     # Provider/Pipeline form components
│   ├── layout/     # App shell, sidebar, header
│   └── ui/         # Base UI primitives
├── pages/          # Route-level page components
│   ├── Advanced/   # Advanced settings pages
│   └── System/     # System management pages
├── utils/          # Helper functions
├── App.tsx         # Root component with routing
└── main.tsx        # Entry point
```

## Key Features

- **Dashboard** - System health, container status, metrics
- **Providers** - Configure AI providers (OpenAI, Deepgram, Local, etc.)
- **Pipelines** - Create modular STT→LLM→TTS pipelines
- **Contexts** - Define conversation contexts and personas
- **Models** - Manage local AI models (download/delete)
- **Setup Wizard** - Guided initial configuration

## Code Splitting

Heavy pages are lazy-loaded to reduce initial bundle size:

- Wizard
- Raw YAML Editor
- Terminal
- Logs
- Models

## Environment Variables

Configure in `vite.config.ts`:

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_URL` | `/api` | Backend API base URL |

## Contributing

1. Run `npm run lint` before committing
2. Run `npm run format` to ensure consistent style
3. Test changes in both dev and production builds
