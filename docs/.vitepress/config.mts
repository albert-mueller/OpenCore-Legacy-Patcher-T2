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
    "DONATE": "Supporting the patcher",
    "LICENSE": "OpenCore Legacy Patcher License",
    "ISSUES-HOLD": "The current hold on new issues and pull requests",
    "TERMS": "OpenCore Patcher Terminology",
    "HOW": "Boot Process with OpenCore Legacy Patcher T2",
    "PATCHEXPLAIN": "Explaining the patches in OpenCore Legacy Patcher T2"
}

const group = (text: string, pages: string[]) => ({
    text,
    collapsed: false,
    items: pages.map((page) => ({ text: titles[page] ?? page, link: `/${page}` })),
})

export default defineConfig({
    title: 'OpenCore Legacy Patcher T2',
    description: 'Guide to put macOS on unsupported devices',
    base: '/OpenCore-Legacy-Patcher-T2/',
    rewrites: {
        'README.md': 'index.md',
    },
    srcExclude: ['node_modules/**'],
    lastUpdated: true,
    ignoreDeadLinks: true,
    head: [
        ['link', { rel: 'icon', href: '/OpenCore-Legacy-Patcher-T2/favicon.ico' }],
        ['meta', { name: 'theme-color', content: '#3eaf7c' }],
        ['meta', { name: 'apple-mobile-web-app-capable', content: 'yes' }],
        ['meta', { name: 'apple-mobile-web-app-status-bar-style', content: 'black' }],
    ],
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
            copyright: 'Copyright © Dortania 2020-2025',
        },
        sidebar: [
            group('Introduction', ['START', 'MODELS', 'FAQ']),
            group('How to install', ['INSTALLER', 'BUILD', 'BOOT', 'POST-INSTALL']),
            group('macOS Support', ['SEQUOIA-DROP', 'SONOMA-DROP', 'VENTURA-DROP', 'MONTEREY-DROP']),
            group('Application', ['UPDATE', 'UNINSTALL', 'PROCESS']),
            group('Troubleshooting', ['TROUBLESHOOT-APP', 'TROUBLESHOOT-MISC', 'TROUBLESHOOT-NONMETAL', 'TROUBLESHOOT-HARDWARE', 'DEBUG']),
            group('Misc', ['TIMEMACHINE', 'ICNS', 'WINDOWS', 'UNIVERSALCONTROL']),
            group('Credit', ['DONATE', 'LICENSE']),
            group('Documentation', ['ISSUES-HOLD', 'TERMS', 'HOW', 'PATCHEXPLAIN']),
        ],
    },
})
