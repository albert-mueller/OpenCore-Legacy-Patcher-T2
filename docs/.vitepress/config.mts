import { defineConfig } from 'vitepress'
import multimdTable from 'markdown-it-multimd-table'

const titles: Record<string, string> = {
    "START": "What is OpenCore?",
    "MODELS": "Supported Models",
    "FAQ": "FAQ",
    "INSTALLER": "Creating macOS Installers",
    "BUILD": "Building and installing OpenCore",
    "BOOT": "Booting OpenCore and macOS",
    "POST-INSTALL": "Post-Installation",
    "SEQUOIA-DROP": "macOS Sequoia",
    "SONOMA-DROP": "macOS Sonoma",
    "VENTURA-DROP": "macOS Ventura",
    "MONTEREY-DROP": "macOS Monterey",
    "UPDATE": "Updating",
    "UNINSTALL": "Uninstall",
    "PROCESS": "Background process",
    "TROUBLESHOOT-APP": "Application issues",
    "TROUBLESHOOT-MISC": "Booting, installer and other issues",
    "TROUBLESHOOT-NONMETAL": "Non-Metal issues",
    "TROUBLESHOOT-HARDWARE": "Hardware issues",
    "DEBUG": "How to debug with OpenCore",
    "TIMEMACHINE": "Restoring Time Machine backup",
    "ICNS": "Creating custom icons for OpenCore and Mac Boot Picker",
    "WINDOWS": "Installing Windows in UEFI Mode",
    "UNIVERSALCONTROL": "Universal Control on unsupported Macs",
    "LICENSE": "OpenCore Legacy Patcher License",
    "TERMS": "OpenCore Patcher Terminology",
    "HOW": "Boot Process with OpenCore Legacy Patcher T2",
    "PATCHEXPLAIN": "Explaining the patches in OpenCore Legacy Patcher T2"
}

// Public URL of the deployed site (GitHub Pages project site, includes `base`).
// Used for the sitemap, canonical links and Open Graph tags so search engines
// and link previews always see absolute, stable URLs.
const SITE_URL = 'https://albert-mueller.github.io/OpenCore-Legacy-Patcher-T2/'
const SITE_TITLE = 'OpenCore Legacy Patcher T2'
const SITE_DESCRIPTION = 'Guide to put macOS on unsupported devices'

// Maps a source page (e.g. "BUILD.md", "index.md") to its public URL.
// cleanUrls is off, so pages are served as *.html.
const pageUrl = (relativePath: string) =>
    SITE_URL + relativePath
        .replace(/(^|\/)(index|README)\.md$/, '$1')
        .replace(/\.md$/, '.html')

const group = (text: string, pages: string[]) => ({
    text,
    collapsed: false,
    items: pages.map((page) => ({ text: titles[page] ?? page, link: `/${page}` })),
})

export default defineConfig({
    lang: 'en-US',
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
    base: '/OpenCore-Legacy-Patcher-T2/',
    rewrites: {
        'README.md': 'index.md',
    },
    srcExclude: ['node_modules/**'],
    lastUpdated: true,
    // Generates /OpenCore-Legacy-Patcher-T2/sitemap.xml at build time.
    // Submit it in Google Search Console / Bing Webmaster Tools.
    sitemap: {
        hostname: SITE_URL,
    },
    ignoreDeadLinks: true,
    head: [
        ['link', { rel: 'icon', href: '/OpenCore-Legacy-Patcher-T2/favicon.ico' }],
        ['meta', { name: 'theme-color', content: '#3eaf7c' }],
        ['meta', { name: 'apple-mobile-web-app-capable', content: 'yes' }],
        ['meta', { name: 'apple-mobile-web-app-status-bar-style', content: 'black' }],
        // Explicitly allow indexing (also the default) and large image previews.
        ['meta', { name: 'robots', content: 'index, follow, max-image-preview:large' }],
        ['meta', { property: 'og:site_name', content: SITE_TITLE }],
        ['meta', { property: 'og:type', content: 'website' }],
        ['meta', { property: 'og:image', content: `${SITE_URL}homepage.png` }],
        ['meta', { name: 'twitter:card', content: 'summary' }],
    ],
    // Per-page SEO tags: canonical URL plus Open Graph title/description/url,
    // so every guide page is indexed under exactly one address.
    transformPageData(pageData) {
        const url = pageUrl(pageData.relativePath)
        const title = pageData.frontmatter.title || pageData.title || SITE_TITLE
        const description = pageData.frontmatter.description || pageData.description || SITE_DESCRIPTION
        pageData.frontmatter.head ??= []
        pageData.frontmatter.head.push(
            ['link', { rel: 'canonical', href: url }],
            ['meta', { property: 'og:url', content: url }],
            ['meta', { property: 'og:title', content: title === SITE_TITLE ? title : `${title} | ${SITE_TITLE}` }],
            ['meta', { property: 'og:description', content: description }],
        )
    },
    markdown: {
        // markdown-it-attrs' table rowspan fix-up hides cells of tables built by
        // markdown-it-multimd-table (rowspan via ^^). The docs don't use {attrs}
        // syntax, so turn it off.
        attrs: { disable: true },
        config: (md) => {
            md.use(multimdTable, { rowspan: true })
        },
    },
    themeConfig: {
        logo: '/homepage.png',
        // The full title is too wide for the sidebar column and overflows past
        // its divider, so use a short name in the nav bar. `title` above is
        // still used for browser tabs and page metadata.
        siteTitle: 'OCLP T2',
        outline: [2, 2],
        socialLinks: [
            { icon: 'github', link: 'https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2/' },
        ],
        editLink: {
            pattern: 'https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2/edit/main/docs/:path',
            text: 'Help us improve this page!',
        },
        search: {
            provider: 'local',
        },
        footer: {
            copyright: 'Copyright © Dortania 2020-2025 · T2 fork © 2026 Albert Müller and OCLP-T2 contributors',
        },
        sidebar: [
            group('Introduction', ['START', 'MODELS', 'FAQ']),
            group('How to install', ['INSTALLER', 'BUILD', 'BOOT', 'POST-INSTALL']),
            group('macOS Support', ['SEQUOIA-DROP', 'SONOMA-DROP', 'VENTURA-DROP', 'MONTEREY-DROP']),
            group('Application', ['UPDATE', 'UNINSTALL', 'PROCESS']),
            group('Troubleshooting', ['TROUBLESHOOT-APP', 'TROUBLESHOOT-MISC', 'TROUBLESHOOT-NONMETAL', 'TROUBLESHOOT-HARDWARE', 'DEBUG']),
            group('Misc', ['TIMEMACHINE', 'ICNS', 'WINDOWS', 'UNIVERSALCONTROL']),
            group('Credit', ['LICENSE']),
            group('Documentation', ['TERMS', 'HOW', 'PATCHEXPLAIN']),
        ],
    },
})
