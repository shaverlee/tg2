"""Configuration Helpers for TurboGears 2"""
import logging
from copy import deepcopy
from collections import MutableMapping as DictMixin
from tg.request_local import config as reqlocal_config

import tg
from tg.util import Bunch
from tg.configuration.utils import get_partial_dict

log = logging.getLogger(__name__)


class DispatchingConfigWrapper(DictMixin):
    """Wrapper for the Dispatching configuration.

    Simple wrapper for the DispatchingConfig object that provides attribute
    style access to the config dictionary.

    This class works by proxying all attribute and dictionary access to
    the underlying DispatchingConfig config object, which is an application local
    proxy that allows for multiple TG2 applications to live
    in the same process simultaneously, but to always get the right
    config data for the application that's requesting them.

    """

    def __init__(self, dict_to_wrap):
        """Initialize the object by passing in config to be wrapped"""
        self.__dict__['config_proxy'] = dict_to_wrap

    def __getitem__(self, key):
        return self.config_proxy.current_conf()[key]

    def __setitem__(self, key, value):
        self.config_proxy.current_conf()[key] = value

    def __getattr__(self, key):
        """Our custom attribute getter.

        Tries to get the attribute off the wrapped object first,
        if that does not work, tries dictionary lookup, and finally
        tries to grab all keys that start with the attribute and
        return sub-dictionaries that can be looked up.

        """
        try:
            return self.config_proxy.__getattribute__(key)
        except AttributeError:
            try:
                return self.config_proxy.current_conf()[key]
            except KeyError:
                return get_partial_dict(key, self.config_proxy.current_conf(), Bunch)

    def __setattr__(self, key, value):
        self.config_proxy.current_conf()[key] = value

    def __delattr__(self, name):
        try:
            del self.config_proxy.current_conf()[name]
        except KeyError:
            raise AttributeError(name)

    def __delitem__(self, key):
        self.__delattr__(key)

    def __len__(self):
        return len(self.config_proxy.current_conf())

    def __iter__(self):
        return iter(self.config_proxy.current_conf())

    def __repr__(self):
        return repr(self.config_proxy.current_conf())

    def keys(self):
        return self.config_proxy.keys()


defaults = {
    'debug': False,
    'package': None,
    'tg.app_globals': None,
    'tg.strict_tmpl_context': True,
    'i18n.lang': None
}

# Push an empty config so all accesses to config at import time have something
# to look at and modify. This config will be merged with the app's when it's
# built in the paste.app_factory entry point.
reqlocal_config.push_process_config(deepcopy(defaults))

#Create a config object that has attribute style lookup built in.
config = DispatchingConfigWrapper(reqlocal_config)


class AppConfig(object):
    __slots__ = ('_configurator', )

    # Attributes and properties that are automatically returned as a view
    # This mostly handles backward compatibility with some oddities of
    # TG<2.4 where some config properties where flat and some were subdicts.
    VIEWS_ATTRIBUTES = {'sa_auth', }

    def __init__(self, **kwargs):
        from .configurator import FullStackApplicationConfigurator
        self._configurator = FullStackApplicationConfigurator()
        self._configurator.update_blueprint(kwargs)

        def _on_config_ready(_, conf):
            self.after_init_config(conf)
        tg.hooks.register('initialized_config', _on_config_ready)

        def _startup_hook(*args, **kwargs):
            tg.hooks.notify('startup', trap_exceptions=True)
        tg.hooks.register('initialized_config', _startup_hook)

        def _before_config_hook(app):
            return tg.hooks.notify_with_value('before_config', app)
        tg.hooks.register('before_wsgi_middlewares', _before_config_hook)

        def _after_config_hook(app):
            return tg.hooks.notify_with_value('after_config', app)
        tg.hooks.register('after_wsgi_middlewares', _after_config_hook)

    def after_init_config(self, conf):
        """
        Override this method to set up configuration variables at the application
        level.  This method will be called after your configuration object has
        been initialized on startup.  Here is how you would use it to override
        the default setting of tg.strict_tmpl_context ::

            from tg import Configurator

            class MyAppConfigurator(Configurator):
                def after_init_config(self, conf):
                    conf['tg.strict_tmpl_context'] = False

            base_config = MyAppConfig()

        """
        pass

    def __setitem__(self, key, value):
        self._configurator.update_blueprint({key: value})

    def __getitem__(self, item):
        if item in self.VIEWS_ATTRIBUTES:
            return self.get_view(item)
        return self._configurator.get_blueprint_value(item)

    def get_view(self, item):
        return self._configurator.get_blueprint_view(item)

    def __setattr__(self, key, value):
        if key not in self.__slots__:
            self.__setitem__(key, value)
        else:
            object.__setattr__(self, key, value)
        return value

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item)

    def register_application_wrapper(self, wrapper, after=None):
        self._configurator.register_application_wrapper(wrapper, after)

    def register_engine(self, factory):
        self._configurator.get('rendering').register_engine(factory)

    def register_controller_wrapper(self, wrapper, controller=None):
        self._configurator.get('dispatch').register_controller_wrapper(wrapper, controller)

    def make_load_environment(self):
        """Return a load_environment function.

        The returned load_environment function can be called to configure
        the TurboGears runtime environment for this particular application.
        You can do this dynamically with multiple nested TG applications
        if necessary.

        """
        return self._configurator.load_environment

    def setup_tg_wsgi_app(self, load_environment=None):
        """Create a base TG app, with all the standard middleware.

        ``load_environment``
            A required callable, which sets up the basic evironment
            needed for the application.
        ``setup_vars``
            A dictionary with all special values necessary for setting up
            the base wsgi app.

        """

        def make_base_app(global_conf=None, wrap_app=None, **app_conf):
            # Configure the Application environment
            init_config = load_environment
            if init_config is None:
                init_config = self.make_load_environment()

            return self._configurator.make_app(init_config(global_conf or {}, app_conf),
                                               wrap_app)

        return make_base_app

    def make_wsgi_app(self, **kwargs):
        return self._configurator.make_wsgi_app(**kwargs)


class OldAppConfig(Bunch):
    """Class to store application configuration.

    This class should have configuration/setup information
    that is *necessary* for proper application function.
    Deployment specific configuration information should go in
    the config files (e.g. development.ini or deployment.ini).

    AppConfig instances have a number of methods that are meant to be
    overridden by users who wish to have finer grained control over
    the setup of the WSGI environment in which their application is run.

    This is the place to configure your application, database,
    transaction handling, error handling, etc.

    Configuration Options provided:

        - ``debug`` -> Enables / Disables debug mode. **Can be set from .ini file**
        - ``serve_static`` -> Enable / Disable serving static files. **Can be set from .ini file**
        - ``use_dotted_templatenames`` -> Use template names as packages in @expose instead of file paths.
          This is usually the default unless TG is started in Minimal Mode. **Can be set from .ini file**
        - ``registry_streaming`` -> Enable streaming of responses, this is enabled by default.
          **Can be set from .ini file**
        - ``use_toscawidgets`` -> Enable ToscaWidgets1, this is deprecated.
        - ``use_toscawidgets2`` -> Enable ToscaWidgets2
        - ``prefer_toscawidgets2`` -> When both TW2 and TW1 are enabled prefer TW2. **Can be set from .ini file**
        - ``custom_tw2_config`` -> Dictionary of configuration options for TW2, refer to
          :class:`.tw2.core.middleware.Config` for available options.
        - ``auth_backend`` -> Authentication Backend, can be ``None``, ``sqlalchemy`` or ``ming``.
        - ``sa_auth`` -> Simple Authentication configuration dictionary.
          This is a Dictionary that contains the configuration options for ``repoze.who``,
          see :ref:`authentication` for available options. Basic options include:

            - ``cookie_secret`` -> Secret phrase used to verify auth cookies.
            - ``authmetadata`` -> Authentication and User Metadata Provider for TurboGears
            - ``post_login_url`` -> Redirect users here after login
            - ``post_logout_url`` -> Redirect users here when they logout
        - ``package`` -> Application Package, this is used to configure paths as being inside a python
        - ``app_globals`` -> Application Globals class, by default build from ``package.lib.app_globals``.
          package. Which enables serving templates, controllers, app globals and so on from the package itself.
        - ``helpers`` -> Template Helpers, by default ``package.lib.helpers`` is used.
        - ``model`` -> The models module (or object) where all the models, DBSession and init_models method are
           available. By default ``package.model`` is used.
        - ``renderers`` -> List of enabled renderers names.
        - ``default_renderer`` -> When not specified, use this renderer for templates.
        - ``auto_reload_templates`` -> Automatically reload templates when modified (disable this on production
          for a performance gain). **Can be set from .ini file**
        - ``use_ming`` -> Enable/Disable Ming as Models storage.
        - ``ming.url`` -> Url of the MongoDB database
        - ``ming.db`` -> If Database is not provided in ``ming.url`` it can be specified here.
        - ``ming.connection.*`` -> Options to configure the ming connection,
          refer to :func:`ming.datastore.create_datastore` for available options.
        - ``use_sqlalchemy`` -> Enable/Disable SQLalchemy as Models storage.
        - ``sqlalchemy.url`` -> Url of the SQLAlchemy database. Refer to :ref:`sqla_master_slave` for
          configuring master-slave urls.
    """
    pass