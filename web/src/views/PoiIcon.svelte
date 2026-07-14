<script lang="ts">
  // One icon from the unified POI language (icons.ts), rendered in HTML: the
  // vendored SVG (served from /static/vendor/poi-icons/svg/) as a CSS mask,
  // tinted by `color` — the same glyph and color the map canvas draws, so
  // chips, list rows, and sheets match the marks and pins.
  let {
    icon,
    color = 'currentColor',
    size = 15,
  }: { icon: string; color?: string; size?: number } = $props()

  const url = $derived(`url(/static/vendor/poi-icons/svg/${icon}.svg)`)
</script>

<span
  class="poi-icon"
  style:width="{size}px"
  style:height="{size}px"
  style:background-color={color}
  style:mask-image={url}
  style:-webkit-mask-image={url}
></span>

<style>
  .poi-icon {
    display: inline-block;
    flex: none;
    vertical-align: -0.15em;
    mask-size: contain;
    mask-repeat: no-repeat;
    mask-position: center;
    -webkit-mask-size: contain;
    -webkit-mask-repeat: no-repeat;
    -webkit-mask-position: center;
  }
</style>
