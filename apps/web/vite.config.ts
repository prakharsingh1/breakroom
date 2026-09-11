import { defineConfig } from "vite";
import vinext from "vinext";
import { cloudflare } from "@cloudflare/vite-plugin";
import { cdnAdapter } from "@vinext/cloudflare/cache/cdn-adapter";

export default defineConfig({
  define: {
    "process.env.NEXT_PUBLIC_BREAKROOM_SITE_ONLY": JSON.stringify("1"),
    "process.env.BREAKROOM_GITHUB_URL": JSON.stringify("https://github.com/prakharsingh1/breakroom"),
  },
  plugins: [
    vinext({
      cache: { cdn: cdnAdapter() },
      prerender: { routes: "*" },
    }),
    cloudflare({
      viteEnvironment: {
        name: "rsc",
        childEnvironments: ["ssr"],
      },
    }),
  ],
});
