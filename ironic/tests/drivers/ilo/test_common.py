# Copyright 2014 Hewlett-Packard Development Company, L.P.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Test class for common methods used by iLO modules."""

import tempfile

import mock
from oslo_config import cfg
from oslo_utils import importutils

from ironic.common import exception
from ironic.common import images
from ironic.common import swift
from ironic.common import utils
from ironic.conductor import task_manager
from ironic.drivers.modules.ilo import common as ilo_common
from ironic.tests.conductor import utils as mgr_utils
from ironic.tests.db import base as db_base
from ironic.tests.db import utils as db_utils
from ironic.tests.objects import utils as obj_utils

ilo_client = importutils.try_import('proliantutils.ilo.client')
ilo_error = importutils.try_import('proliantutils.exception')


CONF = cfg.CONF


class IloValidateParametersTestCase(db_base.DbTestCase):

    def setUp(self):
        super(IloValidateParametersTestCase, self).setUp()
        self.node = obj_utils.create_test_node(self.context,
            driver='fake_ilo', driver_info=db_utils.get_test_ilo_info())

    def test_parse_driver_info(self):
        info = ilo_common.parse_driver_info(self.node)

        self.assertIsNotNone(info.get('ilo_address'))
        self.assertIsNotNone(info.get('ilo_username'))
        self.assertIsNotNone(info.get('ilo_password'))
        self.assertIsNotNone(info.get('client_timeout'))
        self.assertIsNotNone(info.get('client_port'))

    def test_parse_driver_info_missing_address(self):
        del self.node.driver_info['ilo_address']
        self.assertRaises(exception.MissingParameterValue,
                          ilo_common.parse_driver_info, self.node)

    def test_parse_driver_info_missing_username(self):
        del self.node.driver_info['ilo_username']
        self.assertRaises(exception.MissingParameterValue,
                          ilo_common.parse_driver_info, self.node)

    def test_parse_driver_info_missing_password(self):
        del self.node.driver_info['ilo_password']
        self.assertRaises(exception.MissingParameterValue,
                          ilo_common.parse_driver_info, self.node)

    def test_parse_driver_info_invalid_timeout(self):
        self.node.driver_info['client_timeout'] = 'qwe'
        self.assertRaises(exception.InvalidParameterValue,
                          ilo_common.parse_driver_info, self.node)

    def test_parse_driver_info_invalid_port(self):
        self.node.driver_info['client_port'] = 'qwe'
        self.assertRaises(exception.InvalidParameterValue,
                          ilo_common.parse_driver_info, self.node)

    def test_parse_driver_info_missing_multiple_params(self):
        del self.node.driver_info['ilo_password']
        del self.node.driver_info['ilo_address']
        try:
            ilo_common.parse_driver_info(self.node)
            self.fail("parse_driver_info did not throw exception.")
        except exception.MissingParameterValue as e:
            self.assertIn('ilo_password', str(e))
            self.assertIn('ilo_address', str(e))

    def test_parse_driver_info_invalid_multiple_params(self):
        self.node.driver_info['client_timeout'] = 'qwe'
        self.node.driver_info['console_port'] = 'not-int'
        try:
            ilo_common.parse_driver_info(self.node)
            self.fail("parse_driver_info did not throw exception.")
        except exception.InvalidParameterValue as e:
            self.assertIn('client_timeout', str(e))
            self.assertIn('console_port', str(e))


class IloCommonMethodsTestCase(db_base.DbTestCase):

    def setUp(self):
        super(IloCommonMethodsTestCase, self).setUp()
        mgr_utils.mock_the_extension_manager(driver="fake_ilo")
        self.info = db_utils.get_test_ilo_info()
        self.node = obj_utils.create_test_node(self.context,
                driver='fake_ilo', driver_info=self.info)

    @mock.patch.object(ilo_client, 'IloClient', autospec=True)
    def test_get_ilo_object(self, ilo_client_mock):
        self.info['client_timeout'] = 60
        self.info['client_port'] = 443
        ilo_client_mock.return_value = 'ilo_object'
        returned_ilo_object = ilo_common.get_ilo_object(self.node)
        ilo_client_mock.assert_called_with(
            self.info['ilo_address'],
            self.info['ilo_username'],
            self.info['ilo_password'],
            self.info['client_timeout'],
            self.info['client_port'])
        self.assertEqual('ilo_object', returned_ilo_object)

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_get_ilo_license(self, get_ilo_object_mock):
        ilo_advanced_license = {'LICENSE_TYPE': 'iLO 3 Advanced'}
        ilo_standard_license = {'LICENSE_TYPE': 'iLO 3'}

        ilo_mock_object = get_ilo_object_mock.return_value
        ilo_mock_object.get_all_licenses.return_value = ilo_advanced_license

        license = ilo_common.get_ilo_license(self.node)
        self.assertEqual(ilo_common.ADVANCED_LICENSE, license)

        ilo_mock_object.get_all_licenses.return_value = ilo_standard_license
        license = ilo_common.get_ilo_license(self.node)
        self.assertEqual(ilo_common.STANDARD_LICENSE, license)

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_get_ilo_license_fail(self, get_ilo_object_mock):
        ilo_mock_object = get_ilo_object_mock.return_value
        exc = ilo_error.IloError('error')
        ilo_mock_object.get_all_licenses.side_effect = exc
        self.assertRaises(exception.IloOperationError,
                          ilo_common.get_ilo_license,
                          self.node)

    def test_update_ipmi_properties(self):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            ipmi_info = {
                         "ipmi_address": "1.2.3.4",
                         "ipmi_username": "admin",
                         "ipmi_password": "fake",
                         "ipmi_terminal_port": 60
            }
            self.info['console_port'] = 60
            task.node.driver_info = self.info
            ilo_common.update_ipmi_properties(task)
            actual_info = task.node.driver_info
            expected_info = dict(self.info, **ipmi_info)
            self.assertEqual(expected_info, actual_info)

    def test__get_floppy_image_name(self):
        image_name_expected = 'image-' + self.node.uuid
        image_name_actual = ilo_common._get_floppy_image_name(self.node)
        self.assertEqual(image_name_expected, image_name_actual)

    @mock.patch.object(swift, 'SwiftAPI', autospec=True)
    @mock.patch.object(images, 'create_vfat_image', autospec=True)
    @mock.patch.object(utils, 'write_to_file', autospec=True)
    @mock.patch.object(tempfile, 'NamedTemporaryFile', autospec=True)
    def test__prepare_floppy_image(self, tempfile_mock, write_mock,
                                   fatimage_mock, swift_api_mock):
        mock_token_file_obj = mock.MagicMock(spec=file)
        mock_token_file_obj.name = 'token-tmp-file'
        mock_image_file_handle = mock.MagicMock(spec=file)
        mock_image_file_obj = mock.MagicMock(spec=file)
        mock_image_file_obj.name = 'image-tmp-file'
        mock_image_file_handle.__enter__.return_value = mock_image_file_obj
        tempfile_mock.side_effect = iter([mock_image_file_handle,
                                          mock_token_file_obj])

        swift_obj_mock = swift_api_mock.return_value
        self.config(swift_ilo_container='ilo_cont', group='ilo')
        self.config(swift_object_expiry_timeout=1, group='ilo')
        deploy_args = {'arg1': 'val1', 'arg2': 'val2'}
        swift_obj_mock.get_temp_url.return_value = 'temp-url'
        timeout = CONF.ilo.swift_object_expiry_timeout
        object_headers = {'X-Delete-After': timeout}

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:

            task.context.auth_token = 'token'
            temp_url = ilo_common._prepare_floppy_image(task, deploy_args)
            node_uuid = task.node.uuid

        object_name = 'image-' + node_uuid
        files_info = {'token-tmp-file': 'token'}
        write_mock.assert_called_once_with('token-tmp-file', 'token')
        mock_token_file_obj.close.assert_called_once_with()
        fatimage_mock.assert_called_once_with('image-tmp-file',
                                              files_info=files_info,
                                              parameters=deploy_args)

        swift_obj_mock.create_object.assert_called_once_with('ilo_cont',
                object_name, 'image-tmp-file', object_headers=object_headers)
        swift_obj_mock.get_temp_url.assert_called_once_with('ilo_cont',
                object_name, timeout)
        self.assertEqual('temp-url', temp_url)

    @mock.patch.object(swift, 'SwiftAPI', autospec=True)
    @mock.patch.object(images, 'create_vfat_image', autospec=True)
    @mock.patch.object(tempfile, 'NamedTemporaryFile', autospec=True)
    def test__prepare_floppy_image_noauth(self, tempfile_mock, fatimage_mock,
                                          swift_api_mock):
        mock_token_file_obj = mock.MagicMock(spec=file)
        mock_token_file_obj.name = 'token-tmp-file'
        mock_image_file_handle = mock.MagicMock(spec=file)
        mock_image_file_obj = mock.MagicMock(spec=file)
        mock_image_file_obj.name = 'image-tmp-file'
        mock_image_file_handle.__enter__.return_value = mock_image_file_obj
        tempfile_mock.side_effect = iter([mock_image_file_handle])

        self.config(swift_ilo_container='ilo_cont', group='ilo')
        self.config(swift_object_expiry_timeout=1, group='ilo')
        deploy_args = {'arg1': 'val1', 'arg2': 'val2'}

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:

            task.context.auth_token = None
            ilo_common._prepare_floppy_image(task, deploy_args)

        files_info = {}
        fatimage_mock.assert_called_once_with('image-tmp-file',
                                              files_info=files_info,
                                              parameters=deploy_args)

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_attach_vmedia(self, get_ilo_object_mock):
        ilo_mock_object = get_ilo_object_mock.return_value
        insert_media_mock = ilo_mock_object.insert_virtual_media
        set_status_mock = ilo_mock_object.set_vm_status

        ilo_common.attach_vmedia(self.node, 'FLOPPY', 'url')
        insert_media_mock.assert_called_once_with('url', device='FLOPPY')
        set_status_mock.assert_called_once_with(device='FLOPPY',
                boot_option='CONNECT', write_protect='YES')

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_attach_vmedia_fails(self, get_ilo_object_mock):
        ilo_mock_object = get_ilo_object_mock.return_value
        set_status_mock = ilo_mock_object.set_vm_status
        exc = ilo_error.IloError('error')
        set_status_mock.side_effect = exc
        self.assertRaises(exception.IloOperationError,
                          ilo_common.attach_vmedia, self.node,
                          'FLOPPY', 'url')

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_set_boot_mode(self, get_ilo_object_mock):
        ilo_object_mock = get_ilo_object_mock.return_value
        get_pending_boot_mode_mock = ilo_object_mock.get_pending_boot_mode
        set_pending_boot_mode_mock = ilo_object_mock.set_pending_boot_mode
        get_pending_boot_mode_mock.return_value = 'LEGACY'
        ilo_common.set_boot_mode(self.node, 'uefi')
        get_ilo_object_mock.assert_called_once_with(self.node)
        get_pending_boot_mode_mock.assert_called_once_with()
        set_pending_boot_mode_mock.assert_called_once_with('UEFI')

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_set_boot_mode_without_set_pending_boot_mode(self,
                                                         get_ilo_object_mock):
        ilo_object_mock = get_ilo_object_mock.return_value
        get_pending_boot_mode_mock = ilo_object_mock.get_pending_boot_mode
        get_pending_boot_mode_mock.return_value = 'LEGACY'
        ilo_common.set_boot_mode(self.node, 'bios')
        get_ilo_object_mock.assert_called_once_with(self.node)
        get_pending_boot_mode_mock.assert_called_once_with()
        self.assertFalse(ilo_object_mock.set_pending_boot_mode.called)

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_set_boot_mode_with_IloOperationError(self,
                                                  get_ilo_object_mock):
        ilo_object_mock = get_ilo_object_mock.return_value
        get_pending_boot_mode_mock = ilo_object_mock.get_pending_boot_mode
        get_pending_boot_mode_mock.return_value = 'UEFI'
        set_pending_boot_mode_mock = ilo_object_mock.set_pending_boot_mode
        exc = ilo_error.IloError('error')
        set_pending_boot_mode_mock.side_effect = exc
        self.assertRaises(exception.IloOperationError,
                          ilo_common.set_boot_mode, self.node, 'bios')
        get_ilo_object_mock.assert_called_once_with(self.node)
        get_pending_boot_mode_mock.assert_called_once_with()

    @mock.patch.object(ilo_common, 'set_boot_mode', autospec=True)
    def test_update_boot_mode_instance_info_exists(self,
                                                          set_boot_mode_mock):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.node.instance_info['deploy_boot_mode'] = 'bios'
            ilo_common.update_boot_mode(task)
            set_boot_mode_mock.assert_called_once_with(task.node, 'bios')

    @mock.patch.object(ilo_common, 'set_boot_mode', autospec=True)
    def test_update_boot_mode_capabilities_exist(self,
                                                 set_boot_mode_mock):
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.node.properties['capabilities'] = 'boot_mode:bios'
            ilo_common.update_boot_mode(task)
            set_boot_mode_mock.assert_called_once_with(task.node, 'bios')

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_update_boot_mode(self, get_ilo_object_mock):
        ilo_mock_obj = get_ilo_object_mock.return_value
        ilo_mock_obj.get_pending_boot_mode.return_value = 'LEGACY'

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            ilo_common.update_boot_mode(task)
            get_ilo_object_mock.assert_called_once_with(task.node)
            ilo_mock_obj.get_pending_boot_mode.assert_called_once_with()
            self.assertEqual('bios',
                            task.node.instance_info['deploy_boot_mode'])

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_update_boot_mode_unknown(self,
                                      get_ilo_object_mock):
        ilo_mock_obj = get_ilo_object_mock.return_value
        ilo_mock_obj.get_pending_boot_mode.return_value = 'UNKNOWN'
        set_pending_boot_mode_mock = ilo_mock_obj.set_pending_boot_mode

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            ilo_common.update_boot_mode(task)
            get_ilo_object_mock.assert_called_once_with(task.node)
            ilo_mock_obj.get_pending_boot_mode.assert_called_once_with()
            set_pending_boot_mode_mock.assert_called_once_with('UEFI')
            self.assertEqual('uefi',
                           task.node.instance_info['deploy_boot_mode'])

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_update_boot_mode_unknown_except(self,
                                             get_ilo_object_mock):
        ilo_mock_obj = get_ilo_object_mock.return_value
        ilo_mock_obj.get_pending_boot_mode.return_value = 'UNKNOWN'
        set_pending_boot_mode_mock = ilo_mock_obj.set_pending_boot_mode
        exc = ilo_error.IloError('error')
        set_pending_boot_mode_mock.side_effect = exc

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaises(exception.IloOperationError,
                          ilo_common.update_boot_mode, task)
            get_ilo_object_mock.assert_called_once_with(task.node)
            ilo_mock_obj.get_pending_boot_mode.assert_called_once_with()

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_update_boot_mode_legacy(self,
                                     get_ilo_object_mock):
        ilo_mock_obj = get_ilo_object_mock.return_value
        exc = ilo_error.IloCommandNotSupportedError('error')
        ilo_mock_obj.get_pending_boot_mode.side_effect = exc

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            ilo_common.update_boot_mode(task)
            get_ilo_object_mock.assert_called_once_with(task.node)
            ilo_mock_obj.get_pending_boot_mode.assert_called_once_with()
            self.assertEqual('bios',
                           task.node.instance_info['deploy_boot_mode'])

    @mock.patch.object(ilo_common, 'set_boot_mode', autospec=True)
    def test_update_boot_mode_prop_boot_mode_exist(self,
                                                   set_boot_mode_mock):

        properties = {'capabilities': 'boot_mode:uefi'}
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            task.node.properties = properties
            ilo_common.update_boot_mode(task)
            set_boot_mode_mock.assert_called_once_with(task.node, 'uefi')

    @mock.patch.object(images, 'get_temp_url_for_glance_image', autospec=True)
    @mock.patch.object(ilo_common, 'attach_vmedia', autospec=True)
    @mock.patch.object(ilo_common, '_prepare_floppy_image', autospec=True)
    def test_setup_vmedia_for_boot_with_parameters(self, prepare_image_mock,
            attach_vmedia_mock, temp_url_mock):
        parameters = {'a': 'b'}
        boot_iso = 'image-uuid'
        prepare_image_mock.return_value = 'floppy_url'
        temp_url_mock.return_value = 'image_url'

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            ilo_common.setup_vmedia_for_boot(task, boot_iso, parameters)
            prepare_image_mock.assert_called_once_with(task, parameters)
            attach_vmedia_mock.assert_any_call(task.node, 'FLOPPY',
                                               'floppy_url')

            temp_url_mock.assert_called_once_with(task.context, 'image-uuid')
            attach_vmedia_mock.assert_any_call(task.node, 'CDROM', 'image_url')

    @mock.patch.object(swift, 'SwiftAPI', autospec=True)
    @mock.patch.object(ilo_common, 'attach_vmedia', autospec=True)
    def test_setup_vmedia_for_boot_with_swift(self, attach_vmedia_mock,
            swift_api_mock):
        swift_obj_mock = swift_api_mock.return_value
        boot_iso = 'swift:object-name'
        swift_obj_mock.get_temp_url.return_value = 'image_url'
        CONF.keystone_authtoken.auth_uri = 'http://authurl'
        CONF.ilo.swift_ilo_container = 'ilo_cont'
        CONF.ilo.swift_object_expiry_timeout = 1
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            ilo_common.setup_vmedia_for_boot(task, boot_iso)
            swift_obj_mock.get_temp_url.assert_called_once_with('ilo_cont',
                    'object-name', 1)
            attach_vmedia_mock.assert_called_once_with(task.node, 'CDROM',
                    'image_url')

    @mock.patch.object(ilo_common, 'attach_vmedia', autospec=True)
    def test_setup_vmedia_for_boot_with_url(self, attach_vmedia_mock):
        boot_iso = 'http://abc.com/img.iso'
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            ilo_common.setup_vmedia_for_boot(task, boot_iso)
            attach_vmedia_mock.assert_called_once_with(task.node, 'CDROM',
                                                       boot_iso)

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    @mock.patch.object(swift, 'SwiftAPI', autospec=True)
    @mock.patch.object(ilo_common, '_get_floppy_image_name', autospec=True)
    def test_cleanup_vmedia_boot(self, get_name_mock, swift_api_mock,
            get_ilo_object_mock):
        swift_obj_mock = swift_api_mock.return_value
        CONF.ilo.swift_ilo_container = 'ilo_cont'

        ilo_object_mock = mock.MagicMock(spec=['eject_virtual_media'])
        get_ilo_object_mock.return_value = ilo_object_mock
        get_name_mock.return_value = 'image-node-uuid'

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            ilo_common.cleanup_vmedia_boot(task)
            swift_obj_mock.delete_object.assert_called_once_with('ilo_cont',
                    'image-node-uuid')
            ilo_object_mock.eject_virtual_media.assert_any_call('CDROM')
            ilo_object_mock.eject_virtual_media.assert_any_call('FLOPPY')

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_get_secure_boot_mode(self,
                                  get_ilo_object_mock):
        ilo_object_mock = get_ilo_object_mock.return_value
        ilo_object_mock.get_current_boot_mode.return_value = 'UEFI'
        ilo_object_mock.get_secure_boot_mode.return_value = True
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            ret = ilo_common.get_secure_boot_mode(task)
            ilo_object_mock.get_current_boot_mode.assert_called_once_with()
            ilo_object_mock.get_secure_boot_mode.assert_called_once_with()
            self.assertTrue(ret)

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_get_secure_boot_mode_bios(self,
                                       get_ilo_object_mock):
        ilo_object_mock = get_ilo_object_mock.return_value
        ilo_object_mock.get_current_boot_mode.return_value = 'BIOS'
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            ret = ilo_common.get_secure_boot_mode(task)
            ilo_object_mock.get_current_boot_mode.assert_called_once_with()
            self.assertFalse(ilo_object_mock.get_secure_boot_mode.called)
            self.assertFalse(ret)

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_get_secure_boot_mode_fail(self,
                                       get_ilo_object_mock):
        ilo_mock_object = get_ilo_object_mock.return_value
        exc = ilo_error.IloError('error')
        ilo_mock_object.get_current_boot_mode.side_effect = exc

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaises(exception.IloOperationError,
                              ilo_common.get_secure_boot_mode,
                              task)
        ilo_mock_object.get_current_boot_mode.assert_called_once_with()

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_get_secure_boot_mode_not_supported(self,
                                                ilo_object_mock):
        ilo_mock_object = ilo_object_mock.return_value
        exc = ilo_error.IloCommandNotSupportedError('error')
        ilo_mock_object.get_current_boot_mode.return_value = 'UEFI'
        ilo_mock_object.get_secure_boot_mode.side_effect = exc

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaises(exception.IloOperationNotSupported,
                              ilo_common.get_secure_boot_mode,
                              task)
        ilo_mock_object.get_current_boot_mode.assert_called_once_with()
        ilo_mock_object.get_secure_boot_mode.assert_called_once_with()

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_set_secure_boot_mode(self,
                                  get_ilo_object_mock):
        ilo_object_mock = get_ilo_object_mock.return_value
        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            ilo_common.set_secure_boot_mode(task, True)
            ilo_object_mock.set_secure_boot_mode.assert_called_once_with(True)

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_set_secure_boot_mode_fail(self,
                                       get_ilo_object_mock):
        ilo_mock_object = get_ilo_object_mock.return_value
        exc = ilo_error.IloError('error')
        ilo_mock_object.set_secure_boot_mode.side_effect = exc

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaises(exception.IloOperationError,
                              ilo_common.set_secure_boot_mode,
                              task, False)
        ilo_mock_object.set_secure_boot_mode.assert_called_once_with(False)

    @mock.patch.object(ilo_common, 'get_ilo_object', autospec=True)
    def test_set_secure_boot_mode_not_supported(self,
                                                ilo_object_mock):
        ilo_mock_object = ilo_object_mock.return_value
        exc = ilo_error.IloCommandNotSupportedError('error')
        ilo_mock_object.set_secure_boot_mode.side_effect = exc

        with task_manager.acquire(self.context, self.node.uuid,
                                  shared=False) as task:
            self.assertRaises(exception.IloOperationNotSupported,
                              ilo_common.set_secure_boot_mode,
                              task, False)
        ilo_mock_object.set_secure_boot_mode.assert_called_once_with(False)
