# Qitea — TOUR ME (Makkah Tour)

Mobile-style web app for exploring Makkah: food, shopping, landmarks, gifts, and visitor services.

Built with **React + Vite**. The production build is generated into `docs/`, which GitHub Pages serves at [qiteapp.com](https://qiteapp.com).

## Development

```bash
npm install
npm run dev      # local dev server with hot reload
```

## Build & deploy

```bash
npm run build    # outputs the site into docs/
```

Commit the updated `docs/` folder and push — GitHub Pages publishes it automatically. The custom-domain `CNAME` file lives in `public/` so every build copies it into `docs/`.

## Structure

- `src/App.jsx` — app shell, navigation, and all screens (Home, Map, Services, Account, Settings)
- `src/data.js` — activities, services, and navigation content
- `src/index.css` — styling (brand color `#26485B`)
- `indx.html`, `style.css` — the original static prototype, kept for reference
