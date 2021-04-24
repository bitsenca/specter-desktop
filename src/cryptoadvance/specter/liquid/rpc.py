from ..rpc import RpcError, BitcoinRPC


class LiquidRPC(BitcoinRPC):
    """
    This class introduces fixes to Elements RPC that are broken in current Elements Core.
    It also adds custom fields that are normaly not present in the final blind pset, but
    that we find useful.

    In particular this class:
    - combines walletcreatefundedpsbt and blindpsbt calls into one call by default (walletcreatefundedpsbt)
    - TODO: adds support for assets in walletcreatefundedpsbt
    - TODO: adds custom fields to pset:
      - blinding pubkeys for HW recv addr verification
      - ecdh nonce for deterministic range proof
      - extra data that should be encoded in the change rangeproof
    """

    def getbalance(
        self,
        dummy="*",
        minconf=0,
        include_watchonly=True,
        avoid_reuse=False,
        assetlabel="bitcoin",  # pass None to get all
        **kwargs
    ):
        """
        Bitcoin-like getbalance rpc call without assets,
        set assetlabel=None to get balances for all assets
        """
        args = [dummy, minconf, include_watchonly, avoid_reuse]
        # if assetlabel is explicitly set to None - return all assets
        if assetlabel is not None:
            args.append(assetlabel)
        return super().__getattr__("getbalance")(*args, **kwargs)

    def getbalances(self, assetlabel="bitcoin", **kwargs):
        """
        Bitcoin-like getbalance rpc call without assets,
        set assetlabel=None to get balances for all assets
        """
        res = super().__getattr__("getbalances")(**kwargs)
        # if assetlabel is explicitly set to None - return as is
        if assetlabel is None:
            return res
        # otherwise get balances for a particular assetlabel
        for k in res:
            for kk in res[k]:
                v = res[k][kk].get(assetlabel, 0)
                res[k][kk] = v
        return res

    def getreceivedbyaddress(self, address, minconf=1, assetlabel="bitcoin", **kwargs):
        args = [address, minconf]
        if assetlabel is not None:
            args.append(assetlabel)
        return super().__getattr__("getreceivedbyaddress")(*args, **kwargs)

    def walletcreatefundedpsbt(self, inputs, outputs, *args, blind=True, **kwargs):
        """
        Creates and blinds an Elements PSBT transaction.
        Arguments:
        1. inputs: [{txid, vout[, sequence, pegin stuff]}]
        2. outputs: [{address: amount, "asset": asset}, ...] # TODO: add assets support
        3. locktime = 0
        4. options {includeWatching, changeAddress, subtractFeeFromOutputs,
                    replaceable, add_inputs, feeRate, fee_rate}
        5. bip32 derivations
        6. solving data
        7. blind = True - Specter-LiquidRPC specific thing - blind transaction after creation
        """
        res = super().__getattr__("walletcreatefundedpsbt")(
            inputs, outputs, *args, **kwargs
        )
        psbt = res.get("psbt", None)
        # check if we should blind the transaction
        if psbt and blind:
            blinded = self.blindpsbt(psbt)
            # res["unblinded"] = psbt
            res["psbt"] = blinded
        return res

    @classmethod
    def from_bitcoin_rpc(cls, rpc):
        """Convert BitcoinRPC to LiquidRPC"""
        return cls(
            user=rpc.user,
            password=rpc.password,
            host=rpc.host,
            port=rpc.port,
            protocol=rpc.protocol,
            path=rpc.path,
            timeout=rpc.timeout,
            session=rpc.session,
            proxy_url=rpc.proxy_url,
            only_tor=rpc.only_tor,
        )