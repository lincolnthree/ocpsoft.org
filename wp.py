#!/usr/bin/python

# Copyright (c) 2011, tim at brool period com
# Github: github.com/brool/git-wordpress
# Licensed under GPL version 3 -- see http://www.gnu.org/copyleft/gpl.txt for information

# todo: add page support?
# todo: fix slugify (make it match Wordpress's algorithm exactly)

"""wordpress-shuffle -- easy maintenance of WordPress."""

import sys
import os
import os.path
import getopt
import re
import xmlrpclib
try:
    import hashlib as md5
except Exception, e:
    import md5
import functools
import subprocess
import getpass
import tempfile

class BlogXMLRPC:
    """BlogXMLRPC.  Wrapper for the XML/RPC calls to the blog."""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.xrpc = xmlrpclib.ServerProxy(self.url)
        self.get_recent = functools.partial(self.xrpc.metaWeblog.getRecentPosts, 1, self.user, self.password, 5)
        self.new_post = functools.partial(self.xrpc.metaWeblog.newPost, 1, self.user, self.password)
    def get_all(self):
        for post in self.xrpc.metaWeblog.getRecentPosts(1, self.user, self.password, 20):
            yield post
        for post in self.xrpc.metaWeblog.getRecentPosts(1, self.user, self.password, 32767)[20:]:
            yield post
    def get_post(self, post_id): return self.xrpc.metaWeblog.getPost(post_id, self.user, self.password)
    def edit_post(self, post_id, post): return self.xrpc.metaWeblog.editPost(post_id, self.user, self.password, post, True)

class Post:
    """Post.  A set of key => value pairs."""

    # fields that are ignored when comparing signatures
    ignore_fields = set([ 'custom_fields', 'sticky', 'date_created_gmt' ])

    # fields that should not be edited locally
    read_only_fields = set([ 'dateCreated', 'date_created_gmt' ])

    def __init__(self, keys=None):
        self.post = keys and dict(keys) or None

        # make sure all values are in utf-8
        if self.post:
            for k in self.post:
                if isinstance(self.post[k], unicode):
                    self.post[k] = self.post[k].encode('utf8', 'replace')
        
    def __str__(self):
        buffer = []
        lst = self.post.keys()
        lst.sort()
        lst.remove('description')
        for key in lst:
            if key not in Post.ignore_fields:
                buffer.append ( ".%s %s" % (key, self.post[key] ))
        buffer.append(self.post['description']) 
        return '\n'.join(map(lambda x: x, buffer))

    def parse(self, contents):
        self.post = {}

        dots = True
        description = []
        for line in contents.split('\n'):
            if dots and line and line[0] == '.':
                pos = line.find(' ')
                if pos != -1:
                    key = line[1:pos]
                    if key not in Post.ignore_fields:
                        self.post[key] = line[pos+1:]
                else:
                    if key not in Post.ignore_fields:
                        self.post[line[1:]] = ''
            else:
                description.append(line)
                dots = False

        self.post['description'] = '\n'.join(description)
        return self

    def as_dict(self):
        d = dict(self.post)
        for key in Post.read_only_fields: 
            if key in d: del d[key]
        return d

    # Somewhat close to formatting.php/sanitize_title_with_dashes but without the accent handling
    @staticmethod
    def slugify(inStr):
        slug = re.sub(r'<.*?>', '', inStr)
        slug = re.sub(r'(%[a-fA-F0-9]{2})', '---\1---', slug)
        slug = re.sub(r'%', '', slug)
        slug = re.sub(r'---(%[a-fA-F0-9]{2})---', '\1', slug)
        # should remove accents here
        slug = slug.lower()
        slug = re.sub(r'&.+?;', '', slug)
        slug = re.sub(r'[^%a-z0-9 _-]', '', slug)
        slug = re.sub(r'\s+', '-', slug)
        slug = re.sub(r'-+', '-', slug)
        slug = slug.strip('-')

        return slug

    def filename(self):
        fname = self.post.get('wp_slug') or Post.slugify(self.post['title']) or str(self.post['postid'])
        created = str(self.post['dateCreated'])
        if self.post.get('post_status', 'draft') == 'draft':
            return os.path.join('draft', fname)
        else:
            return os.path.join(created[0:4], created[4:6], fname)

    def id(self):
        return int(self.post.get('postid', 0))

    def signature(self):
        return md5.md5(str(self).strip()).digest()

    def write(self, writeTo=None):
        try:
            (dir, filename) = os.path.split(writeTo or self.filename())
            if not os.path.exists(dir): os.makedirs(dir)
            file(writeTo or self.filename(), 'wt').write(str(self))
        except Exception, e:
            print "wp:", e
            pass

def get_changed_files(basedir, xml, maxUnchanged=5):
    """Compare the local file system with the blog to see what files have changed.
    Check blog entries until finding maxUnchanged unmodified entries."""

    created = []
    changed = []

    unchanged = 0
    for post in xml.get_all():
        xml_post = Post(keys=post)
        fname = os.path.join(basedir, xml_post.filename())
        if os.path.exists(fname):
            local_post = Post().parse(file(fname, 'rt').read())
            if xml_post.signature() != local_post.signature():
                changed.append(xml_post)
            else:
                unchanged += 1
                if unchanged > maxUnchanged: break
        else:
            created.append(xml_post)

    return (created, changed)

def download_files(xml):
    """Download all files for the given blog into the current directory."""

    for post in xml.get_all():
        p = Post(keys=post)
        print p.filename()
        if not os.path.exists(p.filename()):
            p.write()
        else:
            print "skipping %s, file in way" % p.filename()

def up_until(fn):
    """Walks up the directory tree until a function that takes a path name returns True."""
    curdir = os.getcwd()
    while True:
        if fn(curdir): return curdir
        newdir = os.path.normpath(os.path.join(curdir, os.path.pardir))
        if (newdir == curdir): return False
        curdir = newdir

if __name__ == "__main__":
    (options, args) = getopt.getopt(sys.argv[1:], "", [ 'diff', 'url=', 'user=', 'password=', 'local' ])
    options = dict(options)

    if len(args) == 0 or args[0] not in ['defaults', 'init', 'status', 'push', 'pull', 'add']:
        print "usage: wp [command]"
        print "where command is:"
        print "    init     -- download everything"
        print "    defaults -- write the defaults for --url, --user, and --password"
        print "    status   -- compare local filesystem vs. blog"
        print "    pull     -- bring down changes to local filesystem"
        print "    push     -- push changes back to the blog"
        print "    add      -- post articles to blog"
        print 
        sys.exit()

    basedir = os.getcwd()

    defaults = {}
    if args[0] != 'defaults':
        try:
            defaults = eval(file(os.path.join(basedir, '.defaults'), 'rt').read())
        except Exception, e:
            pass

    url = defaults.get('url', options.get('--url', None))
    user = defaults.get('user', options.get('--user', None))
    password = defaults.get('password', options.get('--password', None))

    if not url or not user:
        print "wp: need --url, --user"
        sys.exit(1)

    # need to handle defaults here so that password can be optional
    if args[0] == 'defaults':
        defaults = { 'url': url, 'user': user }
        if password: defaults['password'] = password
        file(os.path.join(basedir, '.defaults'), 'wt').write(str(defaults))
        print "wp: defaults written."
        sys.exit(0)

    if not password:
        password = getpass.getpass()

    xml = BlogXMLRPC(url=url, user=user, password=password)

    if args[0] == 'init':
        download_files(xml)

        # create an empty .defaults file to mark the base directory
        if not os.path.exists('.defaults'): file('.defaults', 'wt').write('')
        
    elif args[0] == 'status':
        numToCheck = 5
        if len(args) > 1:
            numToCheck = int(args[1]) if args[1] != 'all' else sys.maxint
        (created, changed) = get_changed_files(basedir, xml, maxUnchanged=numToCheck)

        for p in created:
            print "new on server: %s" % p.filename()
            if '--diff' in options:
                tfn = mktemp()
                p.write(tfn)
                os.system('diff %s %s' % (tfn, p.filename()))
        for p in changed:
            print "differ: %s" % p.filename()
            if '--diff' in options:
                tfn = tempfile.mktemp()
                p.write(tfn)
                os.system('diff %s %s' % (tfn, p.filename()))

    elif args[0] == 'push':
        numToCheck = 5
        if len(args) > 1:
            numToCheck = int(args[1]) if args[1] != 'all' else sys.maxint
        (created, changed) = get_changed_files(basedir, xml, maxUnchanged=numToCheck)

        for xml_post in changed:
            p = Post().parse(file(os.path.join(basedir, xml_post.filename()), 'rt').read())
            xml.edit_post(p.id(), p.as_dict())
            print "local -> server: %s" % xml_post.filename()

    elif args[0] == 'pull': 
        numToCheck = 5
        if len(args) > 1:
            numToCheck = int(args[1]) if args[1] != 'all' else sys.maxint
        (created, changed) = get_changed_files(basedir, xml, maxUnchanged=numToCheck)
        
        for xml_post in created + changed:
            xml_post.write( os.path.join(basedir, xml_post.filename()) )
            print "server -> local: %s" % xml_post.filename()

    elif args[0] == 'add':
        for fname in args[1:]:
            try:
                p = Post().parse( file(fname, 'rt').read() )
                if p.id():
                    xml.edit_post( p.id(), p.as_dict() )
                    print "edited on server: %s" % fname
                else:
                    id = xml.new_post( p.as_dict() )

                    # since it's a new post, get it back again and write it out
                    np = Post(keys = xml.get_post( id ))
                    newname = os.path.join( basedir, np.filename() )
                    np.write(newname)
                    print "posted: %s -> %s" % (fname, np.filename())
            except IOError:
                print "Couldn't open %s, continuing." % fname
            except Exception, e:
                print "wp:", e
                sys.exit(1)

                
        
