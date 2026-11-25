#import "../../build/tokens.typ": *

#let eyebrow(body) = text(fill: token-colors-primary, size: token-typography-label-font-size, weight: token-typography-label-font-weight, tracking: token-typography-label-letter-spacing, body.upper())
#let rule = line(length: 100%, stroke: token-strokes-rule + token-colors-primary)
#let frame(body) = block(width: 100%, inset: token-spacing-lg, body)
