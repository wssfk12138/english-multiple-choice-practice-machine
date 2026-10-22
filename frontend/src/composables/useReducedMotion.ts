import { onBeforeUnmount, ref } from 'vue'

export function useReducedMotion() {
  const media = matchMedia('(prefers-reduced-motion: reduce)')
  const reduced = ref(media.matches)
  const changed = () => { reduced.value = media.matches }
  media.addEventListener('change', changed)
  onBeforeUnmount(() => media.removeEventListener('change', changed))
  return reduced
}
