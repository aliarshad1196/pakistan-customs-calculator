v59: strict live WeBOC Income Tax integration.
Uses WEBOC_PROXY_URL when set, otherwise the deployed Cloudflare Worker URL.
No generic/statutory Income Tax fallback is used for calculator IT.
New diagnostic endpoint: /api/weboc-debug?hs=9503.0090
