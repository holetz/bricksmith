#import "{{tokens.typ}}": *
#import "{{primitives.typ}}": eyebrow, rule, frame

#set text(font: token-typography-body-font-family, size: token-typography-body-font-size, weight: token-typography-body-font-weight)
#show heading.where(level: 1): it => text(size: token-typography-display-font-size, weight: token-typography-display-font-weight, it.body)

#frame[
  #eyebrow[{{field.date}}]
  = {{field.title}}
  #text(fill: token-colors-primary)[{{field.subtitle}}]
  #rule
  {{field.body}}
]
