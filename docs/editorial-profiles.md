# Customize editorial profiles

An editorial profile defines how a site presents homilies. A shared voice, audience and language apply to title, description, and image concept generation. Each creative template adds the instructions specific to its task. Email subject and introduction fields are literal templates rendered without an AI call.

## Edit and activate

Open **Homily Studio → Editorial settings**. Choose a starter voice or write your own, then set the audience and language conventions. Edit the complete title, description, image concept and image rendering instructions. Select a style, palette, mood and image quality.

**Save draft** stores your changes for further work. **Activate draft** saves and activates the current draft for new homilies. Existing homilies retain their stored editorial profile. Saving in another browser can produce a revision conflict; reload before overwriting someone else's changes.

The service always enforces its expected JSON fields, factual instructions, coherent square composition and text-free artwork requirements. Extraction timing, speaker matching, credentials, provider endpoints, models and publishing connections remain service configuration. These are not creative profile fields.

## Write templates

Templates accept these literal placeholders:

| Placeholder | Value |
| --- | --- |
| `{{site_name}}`, `{{voice}}`, `{{audience}}`, `{{language}}` | Shared profile values |
| `{{title}}`, `{{description}}` | Available metadata; empty during initial title/description generation |
| `{{transcript}}` | Source transcript, limited to 4,000 normalized characters for images |
| `{{filename}}` | Recording filename during metadata generation and production email introduction |
| `{{homilist}}` | Confirmed speaker guidance during production metadata generation; otherwise empty |
| `{{style}}`, `{{palette}}`, `{{mood}}` | Selected artistic treatment and profile values |

Placeholders are substituted once; they cannot execute Python, PHP, or template code. Unknown fields and malformed placeholders are rejected. Full factual source context is supplied outside the editable writing instructions. A render style is also applied after concept generation, so it survives a concept-model failure.

For example, title instructions could say:

```text
Write a short, direct title in sentence case. Use an important concrete idea from the homily.
Avoid rhetorical questions, sensational language, and generic phrases such as "A journey of faith".
```

Description instructions could say:

```text
Write three welcoming sentences in {{language}} for {{audience}}.
Name the preached theme, explain its relevance to everyday life, and close with a gentle invitation to listen.
Use the shared site voice. Avoid describing material the preacher did not discuss.
```

These instructions replace the default creative direction. There are no fixed title capitalization or description-length rules competing with them. Prompts guide model output; review remains necessary for factual accuracy and visual quality.

## Test the whole presentation

Choose representative homilies covering different themes. Keep sample inputs and generation quality consistent when comparing profiles. Start with **Title and description** or **Image prompt only**, inspect the results, then render images. **Render this reviewed image prompt** sends the saved final prompt directly to the image model. Another generation can still produce a different image; **Use this image** copies the exact selected artwork to an existing draft.

The **Style library** contains 13 original presets, including the existing sacred editorial treatment and 12 artistic traditions catalogued by [John Hartnup](https://john.hartnup.uk/poster-prompts/). They adapt the traditions to square homily art. Event copy, typography instructions, source example images and prescribed poster subjects are not included. Use the inspiration links to explore each tradition.

Modify `image_render` to refine a treatment, or change the selected preset. Custom instructions can describe materials, colour, lighting, ornament and exclusions. The selected style is always supplied, so choose the nearest preset when writing a variation.

## Share and restore profiles

**Export profile** downloads the current draft as JSON without credentials or test results. Review your editorial text before sharing it. **Import an editorial profile** validates and saves the file as a draft; it does not activate it.

Under **History & setup**, restore a previous active version into the draft. Test or activate it when ready. History is local to the WordPress installation; imported profiles do not carry another site's revision history.
