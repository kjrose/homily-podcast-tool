/* global homilyStudio */
(() => {
  'use strict';
  const root = document.getElementById('homily-studio');
  let workspace, currentTab = 'settings', selectedJob = {}, sources = [], polling = false;
  let labDraft = { filename: homilyStudio.recordingExample, title: '', description: '', transcript: '',
    kind: 'text', compare_active: false, use_local_transcript: false };
  const clone = value => JSON.parse(JSON.stringify(value));
  const el = (tag, attrs = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else node[key] = value;
    }
    for (const child of children.flat()) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return node;
  };
  async function api(path, method = 'GET', body) {
    const response = await fetch(homilyStudio.root + path, {
      method, credentials: 'same-origin', cache: 'no-store',
      headers: { 'X-WP-Nonce': homilyStudio.nonce, 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.message || `Request failed (${response.status})`);
    return value;
  }
  function message(text, error = false) {
    const box = document.getElementById('hs-message');
    box.className = error ? 'notice notice-error' : 'notice notice-success';
    box.replaceChildren(el('p', { text }));
  }
  function action(label, callback, primary = false) {
    return el('button', { type: 'button', class: `button ${primary ? 'button-primary' : ''}`, text: label,
      onclick: async event => {
        const button = event.currentTarget;
        button.disabled = true;
        try { await callback(); } catch (error) { message(error.message, true); }
        finally { button.disabled = false; }
      } });
  }
  function field(label, input, help = '') {
    const id = input.id || `hs-field-${Math.random().toString(36).slice(2)}`;
    input.id = id;
    return el('div', { class: 'hs-field' }, el('label', { htmlFor: id, text: label }), input,
      help ? el('p', { class: 'description', text: help }) : []);
  }
  function select(options, value, attrs = {}) {
    const node = el('select', attrs, options.map(([key, label]) => el('option', { value: key, text: label })));
    node.value = value;
    return node;
  }
  function capture() {
    const labForm = document.getElementById('hs-lab-form');
    if (labForm) {
      const data = new FormData(labForm);
      for (const key of ['filename', 'title', 'description', 'transcript', 'kind']) labDraft[key] = data.get(key);
      for (const key of ['compare_active', 'use_local_transcript']) labDraft[key] = data.get(key) === 'on';
    }
    const form = document.getElementById('hs-profile-form');
    if (!form) return;
    const data = new FormData(form);
    for (const key of ['site_name', 'voice', 'audience', 'language', 'style', 'palette', 'mood', 'image_quality']) {
      workspace.draft[key] = data.get(key);
    }
    workspace.draft.images_enabled = data.get('images_enabled') === 'on';
    for (const key of Object.keys(workspace.draft.prompts)) workspace.draft.prompts[key] = data.get(`prompt_${key}`);
  }
  async function save() {
    capture();
    const saved = await api('draft', 'POST', { profile: workspace.draft, base_revision: workspace.draft.revision });
    workspace.draft = saved;
    message('Draft saved. Active settings are unchanged.');
    return saved;
  }
  function shell() {
    root.replaceChildren(el('h1', { text: 'Homily Studio' }),
      el('p', { text: 'Edit your site’s voice, compare prompt previews, and activate the settings you prefer.' }),
      el('div', { id: 'hs-message', role: 'status', 'ariaLive': 'polite' }),
      el('nav', { class: 'nav-tab-wrapper', 'ariaLabel': 'Workspace sections' },
        [['settings', 'Editorial settings'], ['styles', 'Style library'], ['lab', 'Preview lab'], ['history', 'History & setup']].map(([key, label]) =>
          el('button', { type: 'button', text: label, class: `nav-tab ${currentTab === key ? 'nav-tab-active' : ''}`,
            onclick: () => { capture(); currentTab = key; shell(); show(); } }))),
      el('div', { id: 'hs-content', class: 'hs-content' }));
  }
  function settings() {
    const draft = workspace.draft;
    const form = el('form', { id: 'hs-profile-form', onsubmit: event => event.preventDefault() });
    form.append(el('p', { text: `Active revision: ${workspace.active.revision}. Changes below are drafts until activated.` }));
    form.append(field('Starter voice', select([['', 'Choose a starting voice'], ...Object.keys(workspace.catalog.voices).map(key => [key, key])], '', {
      onchange: event => { if (event.target.value) form.elements.voice.value = workspace.catalog.voices[event.target.value]; }
    })));
    for (const [key, label] of [['site_name', 'Site name'], ['voice', 'Shared site voice'], ['audience', 'Audience'], ['language', 'Language & spelling conventions']]) {
      form.append(field(label, key === 'site_name' || key === 'language'
        ? el('input', { name: key, value: draft[key], type: 'text', maxLength: 10000 })
        : el('textarea', { name: key, value: draft[key], rows: 3, maxLength: 10000 })));
    }
    form.append(field('Image style', select(Object.entries(workspace.catalog.styles).map(([key, style]) => [key, style.name]), draft.style, { name: 'style' })));
    for (const [key, label] of [['palette', 'Palette'], ['mood', 'Image mood']]) {
      form.append(field(label, el('input', { name: key, value: draft[key], type: 'text', maxLength: 10000 })));
    }
    form.append(field('Image quality', select(['low', 'medium', 'high', 'auto'].map(key => [key, key]), draft.image_quality, { name: 'image_quality' })));
    form.append(field('Generate images', el('input', { name: 'images_enabled', type: 'checkbox', checked: draft.images_enabled })));
    form.append(el('h2', { text: 'Creative templates' }), el('p', { class: 'description',
      text: 'Available placeholders: {{site_name}}, {{voice}}, {{audience}}, {{language}}, {{title}}, {{description}}, {{transcript}}, {{filename}}, {{homilist}}, {{style}}, {{palette}}, {{mood}}. Unavailable values are empty. Output formats and factual safeguards are applied by the service.' }));
    const labels = { title: 'Title instructions', description: 'Description instructions', image_concept: 'Image concept instructions',
      image_render: 'Image rendering instructions', success_email_subject: 'Success email subject', success_email_intro: 'Success email introduction' };
    for (const [key, template] of Object.entries(draft.prompts)) {
      form.append(field(labels[key], el('textarea', { name: `prompt_${key}`, value: template, rows: key.startsWith('success_') ? 2 : 5, maxLength: 20000 }),
        key.startsWith('success_') ? 'Literal message template; placeholders are substituted without an AI call.' : 'Complete creative instructions for this task. Shared site voice applies automatically.'));
    }
    form.append(el('div', { class: 'hs-actions' }, action('Save draft', save, true), action('Activate draft', async () => {
      const saved = await save();
      workspace.active = await api('activate', 'POST', { revision: saved.revision });
      workspace.history = (await api('workspace')).history;
      message('Profile activated for future homilies. Existing homilies retain their original profile.');
    }), action('Export profile', () => {
      capture();
      const exported = clone(workspace.draft); exported.revision = 'local';
      const url = URL.createObjectURL(new Blob([JSON.stringify(exported, null, 2)], { type: 'application/json' }));
      const link = el('a', { href: url, download: 'homily-editorial-profile.json' }); link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    })));
    const upload = el('input', { type: 'file', accept: '.json,application/json', onchange: async event => {
      try {
        const file = event.target.files[0];
        if (!file || file.size > 250000) throw new Error('Choose a profile JSON file smaller than 250 KB.');
        const imported = JSON.parse(await file.text());
        workspace.draft = await api('draft', 'POST', { profile: imported, base_revision: workspace.draft.revision });
        shell(); show(); message('Profile imported as a draft. Review and test before activation.');
      } catch (error) { message(error.message, true); }
    } });
    form.append(field('Import an editorial profile', upload, 'Exports include your editorial text. Review them before sharing. Credentials and previews are excluded.'));
    return form;
  }
  function styles() {
    return el('div', {}, el('p', { text: 'These original homily adaptations use the artistic traditions catalogued by John Hartnup. The homily determines the subject; the style determines its treatment.' }),
      el('div', { class: 'hs-grid' }, Object.entries(workspace.catalog.styles).map(([key, style]) =>
        el('article', { class: 'hs-card' }, el('h2', { text: style.name }), el('p', { text: style.category }),
          el('p', { text: style.prompt }), style.source ? el('a', { href: style.source, target: '_blank', rel: 'noopener noreferrer', text: 'Inspiration and examples' }) : [],
          action('Use this style in draft', () => { workspace.draft.style = key; currentTab = 'settings'; shell(); show(); message(`${style.name} selected in the draft.`); })))));
  }
  function inputValues() {
    const form = document.getElementById('hs-lab-form');
    const data = new FormData(form);
    return { kind: data.get('kind'), compare_active: data.get('compare_active') === 'on', use_local_transcript: data.get('use_local_transcript') === 'on',
      input: Object.fromEntries(['title', 'description', 'transcript', 'filename'].map(key => [key, data.get(key)])) };
  }
  async function jobDetail(id, targetPanel = 'hs-preview') {
    selectedJob[targetPanel] = id;
    const data = await api(`jobs/${id}`);
    const panel = document.getElementById(targetPanel);
    if (!panel || selectedJob[targetPanel] !== id) return;
    panel.replaceChildren(el('h2', { text: `${data.label} preview #${id}: ${data.status}` }),
      el('p', { text: `Style: ${workspace.catalog.styles[data.profile.style].name}; quality: ${data.profile.image_quality}; revision: ${data.profile.revision}` }));
    if (data.error) panel.append(el('p', { text: data.error }));
    if (data.result?.metadata) {
      panel.append(el('h3', { text: data.result.metadata.title }), el('p', { text: data.result.metadata.description }));
    }
    if (data.has_image) {
      const image = await api(`jobs/${id}/image`);
      if (selectedJob[targetPanel] !== id || !panel.isConnected) return;
      panel.append(el('img', { src: `data:image/png;base64,${image.image_base64}`, alt: 'Private homily artwork preview', class: 'hs-preview-image' }));
      const target = select([['', 'Choose an existing draft'], ...sources.filter(source => source.status === 'draft').map(source => [String(source.id), source.title])], '');
      const coverUrlField = el('input', { type: 'text', value: 'cover_image' });
      const coverIdField = el('input', { type: 'text', value: 'cover_image_id' });
      panel.append(field('Apply this image to a draft', target,
        'Applying copies this private preview to the WordPress media library. Its media URL becomes publicly accessible.'),
      field('Cover URL metadata key', coverUrlField, 'Match your podcast integration. Leave empty to use only the featured image.'),
      field('Cover ID metadata key', coverIdField),
      action('Use this image on selected draft', async () => {
        if (!target.value) throw new Error('Choose a draft post.');
        await api(`jobs/${id}/apply`, 'POST', { post_id: Number(target.value), cover_url_field: coverUrlField.value, cover_id_field: coverIdField.value });
        message('Selected draft cover image updated. The post remains a draft.');
      }));
    }
    if (data.result) {
      if (data.kind === 'concept' && data.status === 'complete' && data.result.image_prompt) {
        panel.append(action('Render this reviewed image prompt', async () => {
          await api('jobs', 'POST', { concept_job_id: Number(id) });
          message('Image queued using the exact reviewed prompt and its profile snapshot.');
          await refreshJobs();
        }, true));
      }
      for (const [key, label] of [['image_prompt', 'Final image prompt'], ['concept_prompt', 'Concept instructions'], ['analysis_prompt', 'Title/description instructions'], ['usage', 'Models and API usage']]) {
        if (data.result[key]) panel.append(el('details', {}, el('summary', { text: label }),
          el('pre', { text: typeof data.result[key] === 'string' ? data.result[key] : JSON.stringify(data.result[key], null, 2) })));
      }
    }
    panel.append(el('details', {}, el('summary', { text: 'Input and profile snapshot' }), el('pre', { text: JSON.stringify({ input: data.result?.input || data.input, profile: data.profile }, null, 2) })));
    const notes = el('textarea', { rows: 3, value: data.notes || '', maxLength: 5000 });
    const favourite = el('input', { type: 'checkbox', checked: Number(data.favourite) === 1 });
    panel.append(field('Notes', notes), field('Keep this preview', favourite, 'Other previews are deleted after seven days.'), action('Save notes', async () => {
      await api(`jobs/${id}`, 'POST', { notes: notes.value, favourite: favourite.checked }); message('Preview notes saved.');
    }));
  }
  async function refreshJobs() {
    const list = document.getElementById('hs-jobs');
    if (!list || polling) return;
    polling = true;
    try {
      const jobs = await api('jobs');
      if (!document.getElementById('hs-jobs')) return;
      list.replaceChildren(...jobs.map(job => el('div', { class: 'hs-job' },
        action(`${job.label} #${job.id} · ${job.kind} · ${job.status}`, () => jobDetail(job.id)),
        action('Compare', () => jobDetail(job.id, 'hs-comparison')),
        el('span', { text: job.favourite === '1' ? ' Saved' : '' }))));
    } finally { polling = false; }
  }
  function lab() {
    const form = el('form', { id: 'hs-lab-form', onsubmit: event => event.preventDefault() });
    form.append(el('p', { text: 'Tests use the draft currently in this workspace. Comparing with active settings creates two independent jobs. Run the preview worker to process them.' }));
    form.append(field('Use an existing post as sample metadata', select([['', 'Choose a post'], ...sources.map(source => [String(source.id), source.title])], '', {
      onchange: event => {
        const source = sources.find(item => String(item.id) === event.target.value);
        if (source) for (const key of ['title', 'description', 'filename']) form.elements[key].value = source[key];
      }
    }), 'Paste the corresponding homily transcript below to match production context. Published descriptions alone do not include the transcript.'));
    for (const [key, label] of [['filename', 'Recording filename'], ['title', 'Sample title'], ['description', 'Sample description'], ['transcript', 'Homily transcript']]) {
      form.append(field(label, key === 'description' || key === 'transcript'
        ? el('textarea', { name: key, value: labDraft[key], rows: key === 'transcript' ? 7 : 3, maxLength: key === 'transcript' ? 60000 : 10000 })
        : el('input', { name: key, type: 'text', value: labDraft[key] })));
    }
    form.append(field('Use the saved transcript on the homily server', el('input', { name: 'use_local_transcript', type: 'checkbox', checked: labDraft.use_local_transcript }),
      'Available for mapped posts uploaded after Studio was enabled. Leave unchecked when pasting a transcript.'));
    form.append(field('Test', select([['text', 'Title and description'], ['concept', 'Image prompt only'], ['image', 'Image using sample metadata'], ['full', 'Title, description and image']], labDraft.kind, { name: 'kind' })));
    form.append(field('Compare with active profile', el('input', { name: 'compare_active', type: 'checkbox', checked: labDraft.compare_active })));
    form.append(el('p', { text: 'Image previews generate one 1024 × 1024 image per job. With the default gpt-image-1.5 model, estimated image output charges are USD $0.009 low, $0.034 medium, or $0.133 high; input and text-generation charges are additional. Auto quality and other configured models vary. Limit: 20 jobs per UTC day.' }),
      action('Queue preview', async () => {
        const values = inputValues();
        await api('jobs', 'POST', { ...values, profile: workspace.draft });
        message('Preview queued. Refresh the list to see completion; tests do not alter active settings or production posts.');
        await refreshJobs();
      }, true), action('Refresh previews', refreshJobs));
    const panel = el('div', {}, form, el('div', { class: 'hs-lab-results' }, el('div', { id: 'hs-jobs' }),
      el('article', { id: 'hs-preview', class: 'hs-card' }, 'Select a preview.'),
      el('article', { id: 'hs-comparison', class: 'hs-card' }, 'Choose Compare on another preview to view it beside the first.')));
    return panel;
  }
  function history() {
    const panel = el('div', {}, el('h2', { text: 'Previous active profiles' }));
    for (const item of workspace.history) {
      panel.append(el('div', { class: 'hs-job' }, el('span', { text: `${item.changed_at}: ${item.profile.revision} ` }),
        action('Restore as draft', async () => {
          workspace.draft = await api('restore', 'POST', { revision: item.profile.revision });
          currentTab = 'settings'; shell(); show(); message('Revision restored as a draft. Test or activate it when ready.');
        })));
    }
    panel.append(el('h2', { text: 'Connect your homily service' }),
      el('p', { text: 'Create a dedicated user with the Homily Preview Worker role, or Homily Settings Reader if previews are not needed. Generate an Application Password on that user’s profile. Keep the publishing account separate.' }),
      el('p', { text: 'Set wordpress.url to this site’s canonical HTTPS URL, enable homily_studio in the service’s local configuration, and set HOMILY_STUDIO_USER and HOMILY_STUDIO_APP_PASSWORD in its environment.' }),
      el('code', { text: homilyStudio.root + 'settings' }),
      el('p', { text: 'Run python main.py --studio-worker alongside the normal monitor. The worker uses the service’s OpenAI key; this plugin stores no API key.' }),
      el('p', { text: 'Full installation, publishing compatibility, permissions, and troubleshooting instructions are included in docs/wordpress-setup.md in the repository.' }));
    return panel;
  }
  function show() {
    document.getElementById('hs-content').replaceChildren(({ settings, styles, lab, history })[currentTab]());
    if (currentTab === 'lab') refreshJobs().catch(error => message(error.message, true));
  }
  async function start() {
    workspace = await api('workspace');
    sources = await api('sources');
    shell(); show();
    setInterval(() => { if (currentTab === 'lab') refreshJobs().catch(error => message(error.message, true)); }, 5000);
  }
  start().catch(error => root.replaceChildren(el('h1', { text: 'Homily Studio' }), el('p', { text: error.message })));
})();
