#!/usr/bin/env python2.7

import os
import xml.etree.ElementTree as ElementTree
import logging
import datetime
import hashlib
import re
import tarfile

class Magento_Packager(object):
    TARGET_DIRS = {
        'magelocal':        'app/code/local',
        'magecommunity':    'app/code/community',
        'magedesign':       'app/design',
        'mageetc':          'app/etc',
    }

    def __init__(self, base_dir):
        self._base_dir = base_dir
        self._logger = logging.getLogger(self.__class__.__name__)
        self._logger.setLevel(logging.DEBUG)
        self._logger.debug('Packager init with base dir: %s', self._base_dir)

    def build_package_xml(self, connect_file):
        self._logger.info('Building package from connect file: %s', connect_file)
        connect_dom = ElementTree.parse(connect_file)
        ext_name = connect_dom.find('name').text
        self._logger.debug('Using "%s" as extension name', ext_name)
        config_dom = self._get_config_dom(ext_name, connect_dom.find('channel').text)
        module_dom = self._get_module_dom(ext_name)

        self._logger.info('Building extension %s version %s', ext_name,
            config_dom.find('modules/%s/version' % ext_name).text)

        if connect_dom.find('channel').text != \
                module_dom.find('modules/%s/codePool' % ext_name).text:
            self._logger.warning('Connect file code pool (%s) does not match module code pool (%s)',
                connect_dom.find('channel').text,
                module_dom.find('modules/%s/codePool' % ext_name).text)

        pkg_dom = self._build_package_dom(ElementTree.Element('package'),
            connect_dom, config_dom, module_dom)

        self._logger.info('Finished building extension package XML')

        return pkg_dom

    def build_tarball(self, pkg_xml, tarball_name=None):
        if tarball_name is None:
            tarball_name = '%s/build/%s-%s.tgz' % (self._base_dir,
                pkg_xml.findtext('./name'), pkg_xml.findtext('./version'))
        self._logger.info('Writing tarball to: %s', tarball_name)
        cdir = os.getcwd()
        os.chdir(self._base_dir)
        with open('package.xml', 'w') as xml_file:
            ElementTree.ElementTree(pkg_xml).write(xml_file, 'utf-8')
        self._logger.debug('Wrote package XML')
        with tarfile.open(tarball_name, 'w:gz') as tarball:
            tarball.add('app')
            tarball.add('package.xml')
        self._logger.debug('Finished writing tarball')
        os.unlink('package.xml')
        os.chdir(cdir)
        return tarball_name

    def _build_package_dom(self, pkg_dom, connect_dom, config_dom, module_dom):
        ext_name = connect_dom.find('name').text
        now = datetime.datetime.now()
        extension = {
            'name': ext_name,
            'version': config_dom.find('modules/%s/version' % ext_name).text,
            'stability': connect_dom.find('stability').text,
            'license': connect_dom.find('license').text,
            'channel': connect_dom.find('channel').text,
            'extends': None,
            'summary': connect_dom.find('summary').text,
            'description': connect_dom.find('description').text,
            'notes': connect_dom.find('notes').text,
            'authors': None,
            'date': now.date().isoformat(),
            'time': now.time().strftime('%H:%M:%S'),
            'contents': None,
            'compatibile': None,
            'dependencies': None,
        }
        for key, value in extension.iteritems():
            tag = ElementTree.SubElement(pkg_dom, key)
            if value:
                tag.text = value
            self._logger.debug('Added package element <%s> = "%s"', key, value)

        pkg_dom.find('license').set('uri', connect_dom.find('license_uri').text)
        self._build_authors_tag(pkg_dom.find('authors'), connect_dom)
        self._build_contents_tag(pkg_dom.find('contents'), connect_dom)
        self._build_dependencies_tag(pkg_dom.find('dependencies'), connect_dom)
        return pkg_dom

    def _build_authors_tag(self, authors_tag, connect_dom):
        for i, author_name in enumerate(el.text for el in connect_dom.findall('authors/name/name')):
            author_tag = ElementTree.SubElement(authors_tag, 'author')
            name_tag = ElementTree.SubElement(author_tag, 'name')
            name_tag.text = author_name
            user_tag = ElementTree.SubElement(author_tag, 'user')
            user_tag.text = [connect_dom.findtext('authors/user/user')][i]
            email_tag = ElementTree.SubElement(author_tag, 'email')
            email_tag.text = [connect_dom.findtext('authors/email/email')][i]
            self._logger.info('Added author %s (%s) <%s>', name_tag.text,
                user_tag.text, email_tag.text)
        return authors_tag

    def _build_contents_tag(self, contents_tag, connect_dom):
        used_target_paths = list(set(el.text for el in connect_dom.findall('contents/target/target')))
        targets = list(self._iterate_targets(connect_dom))
        for target_path_name in used_target_paths:
            target_tag = ElementTree.SubElement(contents_tag, 'target')
            target_tag.set('name', target_path_name)
            self._logger.debug('Adding objects for target: %s', target_path_name)
            for target in (t for t in targets if t['target'] == target_path_name):
                if target['type'] == 'dir':
                    self._logger.info('Recursively adding dir: %s::%s',
                        target['target'], target['path'])
                    for obj_path, obj_name, obj_hash in self._walk_path(os.path.join(
                                self._base_dir, self.TARGET_DIRS[target['target']], target['path']),
                            target['include'], target['ignore']):
                        parent_tag = self._make_parent_tags(target_tag, obj_path.replace(
                            os.path.join(self._base_dir, self.TARGET_DIRS[target['target']]), '').strip('/'))
                        if obj_hash is None:
                            obj_tag = ElementTree.SubElement(parent_tag, 'dir')
                            obj_tag.set('name', obj_name)
                            self._logger.debug('Added directory: %s', obj_name)
                        else:
                            obj_tag = ElementTree.SubElement(parent_tag, 'file')
                            obj_tag.set('name', obj_name)
                            obj_tag.set('hash', obj_hash)
                            self._logger.debug('Added file: %s (%s)', obj_name, obj_hash)
                else:
                    parent_tag = self._make_parent_tags(target_tag, os.path.dirname(target['path']))
                    obj_name = os.path.basename(target['path'])
                    obj_hash = self._get_file_hash(os.path.join(
                        self._base_dir, self.TARGET_DIRS[target['target']],
                        target['path']))
                    obj_tag = ElementTree.SubElement(parent_tag, 'file')
                    obj_tag.set('name', obj_name)
                    obj_tag.set('hash', obj_hash)
                    self._logger.info('Added single file: %s::%s (%s)',
                        target['target'], target['path'], obj_hash)
        self._logger.debug('Finished adding targets')
        return contents_tag

    def _make_parent_tags(self, target_tag, tag_path):
        if tag_path:
            parts = tag_path.split('/')
            current_node = target_tag
            for part in parts:
                new_node = current_node.find('dir[@name=\'%s\']' % part)
                if new_node is None:
                    new_node = ElementTree.SubElement(current_node, 'dir')
                    new_node.set('name', part)
                current_node = new_node
            return current_node
        else:
            return target_tag

    def _iterate_targets(self, connect_dom):
        for i, el in enumerate(connect_dom.findall('contents/target/target')):
            yield {
                'target':   connect_dom.find('contents/target').getchildren()[i].text,
                'path':     connect_dom.find('contents/path').getchildren()[i].text,
                'type':     connect_dom.find('contents/type').getchildren()[i].text,
                'include':  connect_dom.find('contents/include').getchildren()[i].text,
                'ignore':   connect_dom.find('contents/ignore').getchildren()[i].text,
            }

    def _get_file_hash(self, filename):
        with open(filename, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()

    def _walk_path(self, path, include, ignore):
        for dirpath, dirnames, filenames in os.walk(path):
            for filename in filenames:
                if (include and re.match(include[1:-1], filename) and not \
                        (ignore and re.match(ignore[1:-1], filename))):
                    yield dirpath, filename, self._get_file_hash(os.path.join(dirpath, filename))
            for dirname in dirnames:
                if (include and re.match(include[1:-1], dirname) and not \
                        (ignore and re.match(ignore[1:-1], dirname))):
                    yield dirpath, dirname, None

    def _build_dependencies_tag(self, dependencies_tag, connect_dom):
        req_tag = ElementTree.SubElement(dependencies_tag, 'required')
        php_tag = ElementTree.SubElement(req_tag, 'php')
        min_tag = ElementTree.SubElement(php_tag, 'min')
        min_tag.text = connect_dom.findtext('depends_php_min')
        max_tag = ElementTree.SubElement(php_tag, 'max')
        max_tag.text = connect_dom.findtext('depends_php_max')
        self._logger.debug('Finished adding dependancies')
        return dependencies_tag

    def _get_module_dom(self, ext_name):
        fn = os.path.join(self._base_dir, 'app/etc/modules', ext_name + '.xml')
        self._logger.debug('Using extension config file: %s', fn)
        return ElementTree.parse(fn)

    def _get_config_dom(self, ext_name, codepool):
        ns, ext = ext_name.split('_', 2)
        fn = os.path.join(self._base_dir, 'app/code', codepool, ns, ext, 'etc', 'config.xml')
        self._logger.debug('Using extension module file: %s', fn)
        return ElementTree.parse(fn)

def main(args):
    if len(args) < 2:
        logging.error('Missing package file argument')
    else:
        pkgr = Magento_Packager(os.path.dirname(os.path.dirname(os.path.abspath(args[0]))))
        pkg_xml = pkgr.build_package_xml(args[1])
        tarball = pkgr.build_tarball(pkg_xml)

if __name__ == '__main__':
    import sys
    logging.basicConfig()
    main(sys.argv)
