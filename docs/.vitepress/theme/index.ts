import DefaultTheme from 'vitepress/theme'
import { onMounted, watch, nextTick } from 'vue'
import { useRoute } from 'vitepress'
import mediumZoom from 'medium-zoom'
import './custom.css'

export default {
    extends: DefaultTheme,
    setup() {
        const route = useRoute()
        const initZoom = () => {
            mediumZoom('.vp-doc :not(a) > img', { background: 'var(--vp-c-bg)' })
        }
        onMounted(initZoom)
        watch(() => route.path, () => nextTick(initZoom))
    },
}
