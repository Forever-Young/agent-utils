import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from agent_utils.core import skills


def make(path, text='body', extra=''):
    path.mkdir(parents=True, exist_ok=True)
    (path/'SKILL.md').write_text('---\nname: %s\n%s---\n%s\n' % (path.name, extra, text))
    return path


class SkillsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.home = self.root/'home'; self.home.mkdir()
        self.installed = {'claude', 'kiro-cli', 'agy', 'codex', 'pi'}
        self.env = self.mkenv()

    def tearDown(self):
        self.tmp.cleanup()

    def mkenv(self, project=None):
        return skills.Env(home=self.home, project=project, policy=self.root/'config/skills.json', backups=self.root/'backups',
                          imports=self.root/'data/skills', installed=lambda agent: agent in self.installed)

    def states(self, name):
        return {a: skills.entry_state(self.env, name, a) for a in self.env.dirs}

    def test_link_preview_then_apply(self):
        make(self.env.store/'alpha')
        actions = skills.link(self.env)
        self.assertEqual({a['action'] for a in actions}, {'link'})
        self.assertFalse((self.home/'.claude').exists())
        skills.link(self.env, dry=False)
        for agent in self.env.dirs:
            path = self.env.dirs[agent]/'alpha'
            self.assertTrue(path.is_symlink()); self.assertFalse(os.path.isabs(os.readlink(path)))
            self.assertEqual(path.resolve(), (self.env.store/'alpha').resolve())
        self.assertEqual(skills.link(self.env, dry=False), [])

    def test_link_never_touches_real_directory_or_external_link(self):
        make(self.env.store/'alpha'); make(self.env.store/'beta')
        make(self.env.dirs['claude']/'alpha', 'other')
        outside = make(self.root/'plugin/beta'); (self.env.dirs['kiro-cli']).mkdir(parents=True)
        (self.env.dirs['kiro-cli']/'beta').symlink_to(outside)
        actions = skills.link(self.env, dry=False)
        self.assertTrue(any(a['action'] == 'skip' and a['agent'] == 'claude' for a in actions))
        self.assertFalse((self.env.dirs['claude']/'alpha').is_symlink())
        self.assertEqual((self.env.dirs['kiro-cli']/'beta').resolve(), outside.resolve())
        self.assertEqual(self.states('beta')['kiro-cli'], 'external')

    def test_policy_exclude_and_include(self):
        make(self.env.store/'alpha')
        skills.set_policy(self.env, 'alpha', 'kiro-cli', True, dry=False)
        skills.link(self.env, dry=False)
        self.assertTrue((self.env.dirs['claude']/'alpha').is_symlink())
        self.assertFalse((self.env.dirs['kiro-cli']/'alpha').exists())
        self.assertEqual(self.states('alpha')['kiro-cli'], 'excluded')
        skills.set_policy(self.env, 'alpha', 'claude', True, dry=False)
        self.assertEqual([a['action'] for a in skills.link(self.env, dry=False)], ['unlink'])
        skills.set_policy(self.env, 'alpha', 'claude', False, dry=False)
        skills.set_policy(self.env, 'alpha', 'kiro-cli', False, dry=False)
        self.assertEqual(json.loads((self.root/'config/skills.json').read_text()), {'skills': {}})

    def test_allowlist_policy(self):
        make(self.env.store/'alpha')
        skills.set_policy(self.env, 'alpha', 'kiro-cli', False, dry=True)
        policy = {'skills': {'alpha': {'agents': ['kiro-cli']}}}
        (self.root/'config').mkdir(); (self.root/'config/skills.json').write_text(json.dumps(policy))
        skills.link(self.env, dry=False)
        self.assertTrue((self.env.dirs['kiro-cli']/'alpha').is_symlink())
        self.assertFalse((self.env.dirs['claude']/'alpha').exists())

    def test_adopt_newest_wins_and_others_back_up(self):
        old = make(self.env.store/'alpha', 'old'); mid = make(self.env.dirs['claude']/'alpha', 'mid')
        new = make(self.env.dirs['kiro-cli']/'alpha', 'new')
        now = time.time()
        for path, age in ((old, 300), (mid, 200), (new, 100)):
            os.utime(path/'SKILL.md', (now-age, now-age))
        preview = skills.adopt(self.env, 'alpha')
        self.assertTrue((self.env.dirs['claude']/'alpha').is_dir() and not (self.env.dirs['claude']/'alpha').is_symlink())
        self.assertFalse((self.root/'backups').exists()); self.assertTrue(preview)
        skills.adopt(self.env, 'alpha', dry=False)
        self.assertIn('new', (self.env.store/'alpha/SKILL.md').read_text())
        for agent in self.env.dirs:
            self.assertTrue((self.env.dirs[agent]/'alpha').is_symlink())
        saved = {p.parent.parent.name: p.read_text() for p in (self.root/'backups').glob('*/alpha/SKILL.md')}
        self.assertIn('old', saved['store']); self.assertIn('mid', saved['claude']); self.assertEqual(len(saved), 2)
        self.assertEqual(skills.adopt(self.env, 'alpha', dry=False), [])

    def test_reviewed_store_copy_wins_over_newer_agent_copy(self):
        make(self.env.store/'alpha', 'adapted'); newer = make(self.env.dirs['kiro-cli']/'alpha', 'kiro')
        os.utime(newer/'SKILL.md', (time.time()+100, time.time()+100))
        skills.reviewed(self.env, 'alpha', dry=False); skills.adopt(self.env, 'alpha', dry=False)
        self.assertIn('adapted', (self.env.store/'alpha/SKILL.md').read_text())
        self.assertTrue((self.env.dirs['kiro-cli']/'alpha').is_symlink())

    def test_adopt_store_wins_when_newer(self):
        stored = make(self.env.store/'alpha', 'store'); other = make(self.env.dirs['claude']/'alpha', 'claude')
        now = time.time(); os.utime(other/'SKILL.md', (now-500, now-500)); os.utime(stored/'SKILL.md', (now, now))
        skills.adopt(self.env, 'alpha', dry=False)
        self.assertIn('store', (self.env.store/'alpha/SKILL.md').read_text())
        self.assertTrue((self.env.dirs['claude']/'alpha').is_symlink())

    def test_adopt_respects_exclusion_and_moves_only_copy(self):
        make(self.env.dirs['claude']/'solo', 'x')
        skills.set_policy(self.env, 'solo', 'kiro-cli', True, dry=False)
        skills.adopt(self.env, 'solo', dry=False)
        self.assertTrue((self.env.store/'solo/SKILL.md').is_file())
        self.assertTrue((self.env.dirs['claude']/'solo').is_symlink())
        self.assertFalse((self.env.dirs['kiro-cli']/'solo').exists())

    def test_add_local_records_source_and_links(self):
        src = make(self.root/'src/gamma')
        preview = skills.add(self.env, str(src))
        self.assertFalse(self.env.store.exists()); self.assertIn('add', {a['action'] for a in preview})
        skills.add(self.env, str(src), agents=['kiro-cli'], dry=False)
        self.assertTrue((self.env.store/'gamma/SKILL.md').is_file())
        self.assertTrue((self.env.dirs['kiro-cli']/'gamma').is_symlink())
        self.assertFalse((self.env.dirs['claude']/'gamma').exists())
        entry = json.loads((self.root/'config/skills.json').read_text())['skills']['gamma']
        self.assertEqual(entry['agents'], ['kiro-cli']); self.assertEqual(entry['source'], str(src.resolve()))
        (src/'SKILL.md').write_text('---\nname: gamma\n---\nnew\n')
        skills.add(self.env, str(src), dry=False)
        self.assertIn('new', (self.env.store/'gamma/SKILL.md').read_text())
        self.assertTrue(list((self.root/'backups').glob('store/gamma*')))

    def test_add_requires_skill_md_and_git_subdir(self):
        (self.root/'empty').mkdir()
        with self.assertRaises(ValueError): skills.add(self.env, str(self.root/'empty'))
        repo = self.root/'repo'; make(repo/'sub/delta')
        env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@e', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@e')
        for cmd in (['init', '-q'], ['add', '.'], ['commit', '-qm', 'x']):
            subprocess.run(['git', '-C', str(repo)]+cmd, check=True, env=env)
        skills.add(self.env, 'file://%s#sub/delta' % repo, dry=False)
        self.assertTrue((self.env.store/'delta/SKILL.md').is_file())
        self.assertEqual(json.loads((self.root/'config/skills.json').read_text())['skills']['delta']['source'], 'file://%s#sub/delta' % repo)

    def test_remove_links_only_then_purge(self):
        make(self.env.store/'alpha'); skills.link(self.env, dry=False)
        make(self.env.dirs['claude']/'real'); (self.env.store/'real').mkdir()
        self.assertEqual(len(skills.remove(self.env, 'alpha', dry=True)), 3)
        skills.remove(self.env, 'alpha', agents=['claude'], dry=False)
        self.assertFalse((self.env.dirs['claude']/'alpha').exists())
        self.assertTrue((self.env.dirs['kiro-cli']/'alpha').is_symlink() and (self.env.store/'alpha').exists())
        actions = skills.remove(self.env, 'real', dry=False)
        self.assertEqual(actions[0]['action'], 'skip'); self.assertTrue((self.env.dirs['claude']/'real').is_dir())
        skills.remove(self.env, 'alpha', purge=True, dry=False)
        self.assertFalse((self.env.store/'alpha').exists())
        self.assertTrue(list((self.root/'backups').glob('store/alpha*')))

    def test_broken_link_is_cleaned_by_link(self):
        make(self.env.store/'alpha'); skills.link(self.env, dry=False)
        import shutil; shutil.rmtree(self.env.store/'alpha')
        self.assertEqual(self.states('alpha')['claude'], 'broken')
        skills.link(self.env, dry=False)
        self.assertFalse((self.env.dirs['claude']/'alpha').is_symlink())

    def test_project_scope_uses_project_dirs_and_no_policy(self):
        project = self.root/'proj'; env = self.mkenv(project)
        make(env.store/'alpha'); skills.link(env, dry=False)
        self.assertTrue((project/'.claude/skills/alpha').is_symlink())
        self.assertTrue((project/'.kiro/skills/alpha').is_symlink())
        with self.assertRaises(ValueError): skills.set_policy(env, 'alpha', 'claude', True, dry=False)
        (env.store/'alpha/SKILL.md').write_text('---\nname: alpha\n---\nUse AskUserQuestion\n')
        self.assertEqual(len(skills.check(env)['adapt']), 1)
        skills.reviewed(env, 'alpha', dry=False)
        self.assertEqual(skills.check(env)['adapt'], [])

    def test_status_rows_and_cli(self):
        make(self.env.store/'alpha'); make(self.env.dirs['claude']/'beta')
        rows = {r['name']: r for r in skills.status(self.env)}
        self.assertEqual(rows['alpha']['agents'], {'claude': 'missing', 'kiro-cli': 'missing', 'agy': 'missing'})
        self.assertEqual(rows['beta']['agents']['claude'], 'only-here')
        self.assertEqual(skills.agent_name('kiro'), 'kiro-cli')
        self.assertEqual(skills.agent_name('antigravity'), 'agy')
        with self.assertRaises(ValueError): skills.agent_name('nope')

    def test_managed_skills_are_never_touched(self):
        aws = make(self.env.store/'aws-x'); (aws/'.aws-skill-metadata').write_text('{}')
        copy = make(self.env.dirs['claude']/'aws-x'); (copy/'.aws-skill-metadata').write_text('{}')
        make(self.env.store/'plannotator-review', 'shared'); make(self.env.dirs['kiro-cli']/'plannotator-review', 'kiro')
        twg = make(self.env.dirs['kiro-cli']/'twg'); (twg/'.twg-install.json').write_text('{}')
        self.assertEqual(self.states('aws-x'), {'claude': 'managed', 'kiro-cli': 'managed', 'agy': 'managed'})
        self.assertEqual(self.states('twg')['kiro-cli'], 'managed')
        self.assertEqual(skills.link(self.env, dry=False), [])
        for name in ('aws-x', 'plannotator-review', 'twg'): self.assertEqual(skills.adopt(self.env, name, dry=False), [])
        self.assertTrue(copy.is_dir() and not copy.is_symlink()); self.assertFalse((self.root/'backups').exists())
        with self.assertRaises(ValueError): skills.add(self.env, str(make(self.root/'src/aws-x')))
        rows = {r['name']: r for r in skills.status(self.env)}
        self.assertEqual(rows['aws-x']['owner'], 'AWS agent toolkit'); self.assertEqual(rows['twg']['owner'], 'twg')

    def test_lock_store_copy_wins_and_variants_stay(self):
        make(self.env.store/'cc', 'upstream'); kiro = make(self.env.dirs['kiro-cli']/'cc', 'adapted')
        make(self.env.dirs['claude']/'cc', 'upstream')
        os.utime(kiro/'SKILL.md', (time.time()+100, time.time()+100))
        (self.home/'.agents/.skill-lock.json').write_text(json.dumps({'skills': {'cc': {'source': 'someone/repo'}}}))
        self.assertEqual(self.states('cc'), {'claude': 'same', 'kiro-cli': 'variant', 'agy': 'missing'})
        skills.adopt(self.env, 'cc', dry=False)
        self.assertIn('upstream', (self.env.store/'cc/SKILL.md').read_text())
        self.assertTrue((self.env.dirs['claude']/'cc').is_symlink())
        self.assertIn('adapted', (kiro/'SKILL.md').read_text()); self.assertFalse(kiro.is_symlink())
        self.assertEqual(skills.status(self.env)[0]['source'], 'someone/repo')

    def test_duplicates_in_store_readers(self):
        make(self.env.store/'alpha'); make(self.home/'.pi/agent/skills/alpha'); make(self.home/'.codex/skills/solo')
        found = {(d['agent'], d['skill']): d['kind'] for d in skills.duplicates(self.env)}
        self.assertEqual(found, {('pi', 'alpha'): 'same', ('codex', 'solo'): 'only-here'})

    def test_sync_moves_reader_skills_renames_and_dedupes(self):
        make(self.home/'.codex/skills/apple-notes')
        make(self.home/'.cursor/skills/cli', extra='').joinpath('SKILL.md').write_text('---\nname: circleci-cli\n---\nx\n')
        aws = make(self.env.store/'aws-x'); (aws/'.aws-skill-metadata').write_text('{}')
        pi = self.home/'.pi/agent/skills/aws-x'; pi.parent.mkdir(parents=True); shutil.copytree(aws, pi)
        make(self.env.store/'plannotator-review', 'shared'); make(self.home/'.vibe/skills/plannotator-review', 'vibe')
        preview = skills.sync(self.env)
        self.assertTrue(preview); self.assertTrue((self.home/'.codex/skills/apple-notes').is_dir())
        skills.sync(self.env, dry=False)
        self.assertTrue((self.env.store/'apple-notes/SKILL.md').is_file())
        self.assertFalse((self.home/'.codex/skills/apple-notes').exists())
        self.assertTrue((self.env.store/'circleci-cli/SKILL.md').is_file())
        self.assertFalse((self.home/'.cursor/skills/cli').exists())
        self.assertTrue((self.env.dirs['claude']/'circleci-cli').is_symlink())
        self.assertFalse((self.env.dirs['claude']/'cli').exists())
        self.assertFalse(pi.exists()); self.assertTrue(list((self.root/'backups').glob('pi/aws-x')))
        self.assertIn('vibe', (self.home/'.vibe/skills/plannotator-review/SKILL.md').read_text())
        self.assertFalse((self.env.dirs['claude']/'aws-x').exists())
        self.assertEqual([a for a in skills.sync(self.env, dry=False) if a['action'] != 'skip'], [])

    def test_adopt_ignores_folder_without_skill_md(self):
        (self.env.dirs['claude']/'notes').mkdir(parents=True); make(self.home/'.codex/skills/notes', 'real')
        skills.sync(self.env, dry=False)
        self.assertIn('real', (self.env.store/'notes/SKILL.md').read_text())
        self.assertTrue((self.env.dirs['claude']/'notes').is_symlink())

    def test_remove_purge_backs_up_real_copies(self):
        make(self.env.dirs['claude']/'agterm'); make(self.home/'.codex/skills/agterm')
        skills.remove(self.env, 'agterm', purge=True, dry=False)
        self.assertFalse((self.env.dirs['claude']/'agterm').exists()); self.assertFalse((self.home/'.codex/skills/agterm').exists())
        self.assertTrue((self.root/'backups/claude/agterm').is_dir()); self.assertTrue((self.root/'backups/codex/agterm').is_dir())

    def test_reader_cannot_be_excluded(self):
        with self.assertRaises(ValueError): skills.agent_name('codex')
        self.assertEqual(skills.agent_name('codex', linked_only=False), 'codex')

    def plugin(self, version, text='Use AskUserQuestion to ask.'):
        path = make(self.home/'.claude/plugins/cache/mkt/plug'/version/'skills/tool', text)
        return path

    def test_market_skills_newest_version_and_check_offer(self):
        old = self.plugin('1.0'); new = self.plugin('2.0')
        os.utime(old.parent.parent, (time.time()-500, time.time()-500))
        found = skills.market_skills(self.env)
        self.assertEqual(found['tool']['owners'], {'claude'})
        self.assertEqual([s['path'] for s in found['tool']['sources']], [str(new)])
        offer = skills.check(self.env)['offer']
        self.assertEqual(offer[0]['skill'], 'tool'); self.assertIn('AskUserQuestion', offer[0]['uses'])
        make(self.home/'.codex/plugins/cache/openai-curated/x/1/skills/codex-only')
        self.assertNotIn('codex-only', {o['skill'] for o in skills.check(self.env)['offer']})
        self.assertIn('codex-only', {o['skill'] for o in skills.check(self.env, all_markets=True)['offer']})
        skills.dismiss(self.env, ['tool'], dry=False)
        self.assertNotIn('tool', {o['skill'] for o in skills.check(self.env)['offer']})

    def test_import_links_lacking_agents_and_tracks_source(self):
        src = self.plugin('1.0')
        preview = skills.import_skill(self.env, str(src))
        self.assertFalse((self.root/'data').exists())
        self.assertEqual({a['agent'] for a in preview if a['action'] == 'link'}, {'kiro-cli', 'agy', 'codex', 'pi'})
        skills.import_skill(self.env, str(src), dry=False)
        imported = self.root/'data/skills/tool'
        self.assertTrue((imported/skills.IMPORT_MARK).is_file())
        self.assertFalse((self.env.dirs['claude']/'tool').exists())
        self.assertEqual((self.home/'.codex/skills/tool').resolve(), imported.resolve())
        report = skills.check(self.env)
        self.assertEqual([a['skill'] for a in report['adapt']], ['tool']); self.assertEqual(report['offer'], [])
        (imported/'SKILL.md').write_text('---\nname: tool\n---\nAsk the user (AskUserQuestion in Claude Code).\n')
        skills.reviewed(self.env, 'tool', dry=False)
        self.assertEqual(skills.check(self.env)['adapt'], [])
        (src/'SKILL.md').write_text('changed upstream')
        self.assertEqual([s['skill'] for s in skills.check(self.env)['stale']], ['tool'])
        (self.home/'.pi/agent/skills/tool').unlink(); self.installed.add('kimi')
        relinked = {a['agent'] for a in skills.relink_imports(self.env, dry=False)}
        self.assertEqual(relinked, {'pi', 'kimi'})
        skills.remove(self.env, 'tool', purge=True, dry=False)
        self.assertFalse(imported.exists()); self.assertFalse((self.home/'.codex/skills/tool').is_symlink())

    def test_check_adapt_store_and_reviewed(self):
        make(self.env.store/'own', 'Run ${CLAUDE_PLUGIN_ROOT}/x.sh')
        self.assertEqual(skills.check(self.env)['adapt'][0]['uses'], {'${CLAUDE_*} variable': 1})
        skills.reviewed(self.env, 'own', dry=False)
        self.assertEqual(skills.check(self.env)['adapt'], [])
        (self.env.store/'own/SKILL.md').write_text('---\nname: own\n---\nUse AskUserQuestion\n')
        self.assertEqual(len(skills.check(self.env)['adapt']), 1)


if __name__ == '__main__':
    unittest.main()
